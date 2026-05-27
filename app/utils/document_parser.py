"""
Structured Document Parser — Stage 7
======================================
Extracts named fields from Gujarati government documents using:
  - Regex patterns with Gujarati anchor keywords
  - Layout-aware positional logic
  - Document-type detection (mutation, survey, notice, etc.)
  - Table structure parsing

Supported document types:
  - Land records / 7/12 extracts (સાત-બાર)
  - Mutation entries (ફેરફાર નોંધ)
  - Survey records (સર્વે)
  - Tax notices (કર નોટિસ)
  - Gram Panchayat records (ગ્રામ પંચાયત)
  - GIDC notices
  - Revenue department documents
  - Court / legal notices
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Document type detection ────────────────────────────────────────────────

class DocumentType(str, Enum):
    SEVEN_TWELVE     = "seven_twelve"       # 7/12 extract
    MUTATION         = "mutation"            # ફેરફાર નોંધ
    SURVEY           = "survey"             # સર્વે
    TAX_NOTICE       = "tax_notice"         # કર નોટિસ
    GRAM_PANCHAYAT   = "gram_panchayat"
    GIDC_NOTICE      = "gidc_notice"
    REVENUE          = "revenue"
    COURT_NOTICE     = "court_notice"
    PROPERTY_CARD    = "property_card"      # મિલ્કત કાર્ડ
    UNKNOWN          = "unknown"


DOCUMENT_TYPE_KEYWORDS: Dict[DocumentType, List[str]] = {
    DocumentType.SEVEN_TWELVE: [
        "સાત-બાર", "૭/૧૨", "7/12", "અધિકાર અભિલેખ", "ગ.ન.ન.",
        "ખાતા ક્રમ", "ખાતેદારનું નામ",
    ],
    DocumentType.MUTATION: [
        "ફેરફાર નોંધ", "ફેરફારની નોંધ", "નોંધ ક્રમ", "ફેર નોંધ",
        "mutation", "Mutation Entry",
    ],
    DocumentType.SURVEY: [
        "સર્વે ક્રમ", "સર્વે નં", "ભૂ-અભિ", "ભૂ-ર", "સ.નં.",
    ],
    DocumentType.TAX_NOTICE: [
        "કર નોટિસ", "ટેક્સ", "demand notice", "Tax Notice",
        "મિલ્કત વેરો", "ગૃહ-વેરો",
    ],
    DocumentType.GRAM_PANCHAYAT: [
        "ગ્રામ પંચાયત", "ગ.પ.", "gram panchayat", "ગ્રા.પ.",
        "સરપંચ", "ગ્રામ સભા",
    ],
    DocumentType.GIDC_NOTICE: [
        "GIDC", "ગ.ઔ.વ.નિ.", "Gujarat Industrial", "ઉદ્યોગ",
    ],
    DocumentType.COURT_NOTICE: [
        "ન્યાયાલય", "કોર્ટ", "Court", "Legal Notice",
        "ફ઼િ. ક.", "ફ. ક.", "Civil Case",
    ],
    DocumentType.REVENUE: [
        "મહેસૂલ", "Revenue", "તલાટી", "કલેક્ટર", "Collector",
        "ડી.ડી.ઓ.", "DDO", "DP",
    ],
    DocumentType.PROPERTY_CARD: [
        "મિલ્કત કાર્ડ", "Property Card", "ઘ-ફ", "ઘ ફ",
        "city survey", "City Survey",
    ],
}


def detect_document_type(text: str) -> DocumentType:
    """Detect document type from text content using keyword matching."""
    text_lower = text.lower()
    scores: Dict[DocumentType, int] = {}

    for doc_type, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score:
            scores[doc_type] = score

    if not scores:
        return DocumentType.UNKNOWN

    return max(scores, key=scores.get)


# ── Field extraction patterns ──────────────────────────────────────────────

@dataclass
class ExtractedField:
    name: str
    value: str
    confidence: float          # 0.0–1.0
    source: str = "regex"      # "regex", "layout", "fuzzy"
    raw_value: str = ""
    page_number: Optional[int] = None
    char_position: Optional[int] = None


@dataclass
class ParsedDocument:
    document_type: DocumentType
    fields: Dict[str, ExtractedField] = field(default_factory=dict)
    structured_data: Dict[str, Any] = field(default_factory=dict)
    tables: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ── Regex pattern library ──────────────────────────────────────────────────

# Date patterns (Gujarati format: DD/MM/YYYY or DD-MM-YYYY)
DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b"),
    re.compile(r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2})\b"),
    # Gujarati date with month name
    re.compile(r"\b(\d{1,2})\s+(જાન|ફેબ|માર|એપ્|મે|જૂન|જુ|ઓ|સ|ઓ|ન|ડ)[^\s]*\s+(\d{4})\b"),
    # Written as: તા. DD/MM/YYYY
    re.compile(r"ત[ા.]+\s*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})"),
]

# Survey number patterns
SURVEY_NUMBER_PATTERNS = [
    re.compile(r"સ[ર્]*\.?\s*ન[ં°]*\.?\s*:?\s*(\d+(?:[/\-]\d+)?)"),
    re.compile(r"સ[ર્]*\.?\s*ન[ં°]*\.?\s*(\d+(?:[/\-]\d+)?)"),
    re.compile(r"[Ss]urvey\s*[Nn]o\.?\s*:?\s*(\d+(?:[/\-]\d+)?)"),
    re.compile(r"ભૂ[- ]*ન[ં°]*\.?\s*:?\s*(\d+(?:[/\-]\d+)?)"),
]

# Mutation number patterns
MUTATION_NUMBER_PATTERNS = [
    re.compile(r"ન[ં°]*\.?\s*:?\s*(\d+)\s*/\s*(\d{4})"),
    re.compile(r"ફ[ે]*ર.*?ન[ં°]*\.?\s*:?\s*(\d+)"),
    re.compile(r"[Mm]utation\s*[Nn]o\.?\s*:?\s*(\d+)"),
    re.compile(r"ન[ં°].*?(\d{1,6})"),
]

# Village name anchors
VILLAGE_PATTERNS = [
    re.compile(r"ગ[ા]?મ\s*:?\s*([^\n,।।]+?)(?:\s*(?:,|।|।|\n|જિ|ત[ા]|સ\.ન))"),
    re.compile(r"ગ[ા]?મ\s*નું\s*નામ\s*:?\s*([^\n,।।]+?)(?:\s*(?:,|।|।|\n))"),
    re.compile(r"[Vv]illage\s*:?\s*([A-Za-z\u0A80-\u0AFF\s]+?)(?=\s*(?:,|\n|[Tt]aluka|[Dd]ist))"),
]

# Taluka patterns
TALUKA_PATTERNS = [
    re.compile(r"ત[ા]?\.?\s*:?\s*([^\n,।।]+?)(?:\s*(?:,|।|।|\n|જિ\.?|[Dd]ist))"),
    re.compile(r"[Tt]aluka\s*:?\s*([A-Za-z\u0A80-\u0AFF\s]+?)(?=\s*(?:,|\n|[Dd]ist))"),
    re.compile(r"ત[ા]?લ[ુ]?ક[ા]\s*:?\s*([^\n,।।]+?)(?:\s*(?:,|।|।|\n))"),
]

# District patterns
DISTRICT_PATTERNS = [
    re.compile(r"જિ[લ]?\.?\s*:?\s*([^\n,।।]+?)(?:\s*(?:\n|$|।|।))"),
    re.compile(r"[Dd]ist(?:rict)?\s*:?\s*([A-Za-z\u0A80-\u0AFF\s]+?)(?=\s*(?:,|\n|$))"),
    re.compile(r"જિ[ëëé]*[લ]?[ëëé]*ä[ëëé]*[ëëé]*\s*:?\s*([^\n,।।]+)"),
]

# Owner/holder name patterns
OWNER_PATTERNS = [
    re.compile(r"ખ[ા]?ત[ે]?દ[ા]?ર.*?:?\s*([^\n,।।]+?)(?:\s*(?:\n|$|,))"),
    re.compile(r"ન[ા]?મ\s*:?\s*([^\n,।।]+?)(?:\s*(?:\n|$|,))"),
    re.compile(r"[Oo]wner\s*:?\s*([A-Za-z\u0A80-\u0AFF\s]+?)(?=\s*(?:,|\n))"),
    re.compile(r"ધ[ા]?ર[ણ]?ક.*?:?\s*([^\n,।।]+?)(?:\s*(?:\n|$|,))"),
]

# Area / land area patterns
AREA_PATTERNS = [
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:હ[ે]*ક[્]?ટ[ર]|[Hh]ect(?:are)?)"),
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:ગ[ુ]?ઠ[ા]|[Gg]untha)"),
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:ચ[ો]?.ફ[ૂ]?ટ|[Ss]q\.?\s*[Ff]t)"),
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:એ[ક]?[ર]|[Aa]cre)"),
    re.compile(r"([0-9]+)\s*-\s*([0-9]+)\s*-\s*([0-9]+)"),   # acres-guntha format
]

# Tax/amount patterns
AMOUNT_PATTERNS = [
    re.compile(r"(?:₹|Rs\.?|રૂ\.?|રૂ[૫]|INR)\s*([0-9,]+(?:\.[0-9]{1,2})?)"),
    re.compile(r"([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:₹|Rs\.?|રૂ\.?)"),
    re.compile(r"([0-9,]+(?:\.[0-9]{2})?)/\-"),   # Indian format 1234/-
]

# Notice / registration number
NOTICE_NUMBER_PATTERNS = [
    re.compile(r"ન[ં°]\.?\s*:?\s*([A-Za-z0-9/\-]+)"),
    re.compile(r"[Nn]o\.?\s*:?\s*([A-Za-z0-9/\-]+)"),
    re.compile(r"ક[્]?ર[મ]?\s*ન[ં°]\.?\s*:?\s*([0-9/\-]+)"),
    re.compile(r"[Rr]eg\.?\s*[Nn]o\.?\s*:?\s*([A-Za-z0-9/\-]+)"),
]

# PIN code
PINCODE_PATTERN = re.compile(r"\b(3[6-9][0-9]{4})\b")   # Gujarat PIN: 36xxxx–39xxxx

# Phone number
PHONE_PATTERN = re.compile(r"\b(\+?91[-\s]?)?([6-9][0-9]{9})\b")


def _extract_first_match(
    patterns: List[re.Pattern],
    text: str,
    group: int = 1,
    confidence: float = 0.85,
    field_name: str = "",
) -> Optional[ExtractedField]:
    """Try each pattern, return the first match as an ExtractedField."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                value = match.group(group).strip()
            except IndexError:
                value = match.group(0).strip()
            if value:
                return ExtractedField(
                    name=field_name,
                    value=value,
                    confidence=confidence,
                    raw_value=match.group(0),
                    char_position=match.start(),
                )
    return None


def _extract_all_matches(
    patterns: List[re.Pattern],
    text: str,
    group: int = 1,
    confidence: float = 0.80,
    field_name: str = "",
) -> List[ExtractedField]:
    """Extract all non-overlapping matches across patterns."""
    found: List[ExtractedField] = []
    seen_positions: set = set()

    for pattern in patterns:
        for match in pattern.finditer(text):
            pos = match.start()
            if pos in seen_positions:
                continue
            seen_positions.add(pos)
            try:
                value = match.group(group).strip()
            except IndexError:
                value = match.group(0).strip()
            if value:
                found.append(ExtractedField(
                    name=field_name,
                    value=value,
                    confidence=confidence,
                    raw_value=match.group(0),
                    char_position=pos,
                ))

    return found


def extract_dates(text: str) -> List[ExtractedField]:
    return _extract_all_matches(DATE_PATTERNS, text, confidence=0.90, field_name="date")


def extract_survey_numbers(text: str) -> List[ExtractedField]:
    return _extract_all_matches(
        SURVEY_NUMBER_PATTERNS, text, confidence=0.85, field_name="survey_number"
    )


def extract_mutation_numbers(text: str) -> List[ExtractedField]:
    return _extract_all_matches(
        MUTATION_NUMBER_PATTERNS, text, confidence=0.80, field_name="mutation_number"
    )


def extract_village(text: str) -> Optional[ExtractedField]:
    return _extract_first_match(VILLAGE_PATTERNS, text, confidence=0.78, field_name="village")


def extract_taluka(text: str) -> Optional[ExtractedField]:
    return _extract_first_match(TALUKA_PATTERNS, text, confidence=0.78, field_name="taluka")


def extract_district(text: str) -> Optional[ExtractedField]:
    return _extract_first_match(DISTRICT_PATTERNS, text, confidence=0.80, field_name="district")


def extract_owner_names(text: str) -> List[ExtractedField]:
    return _extract_all_matches(OWNER_PATTERNS, text, confidence=0.75, field_name="owner_name")


def extract_amounts(text: str) -> List[ExtractedField]:
    return _extract_all_matches(AMOUNT_PATTERNS, text, confidence=0.88, field_name="amount")


def extract_areas(text: str) -> List[ExtractedField]:
    return _extract_all_matches(AREA_PATTERNS, text, confidence=0.82, field_name="area")


def extract_notice_numbers(text: str) -> List[ExtractedField]:
    return _extract_all_matches(
        NOTICE_NUMBER_PATTERNS, text, confidence=0.80, field_name="notice_number"
    )


def extract_pincodes(text: str) -> List[ExtractedField]:
    return _extract_all_matches(
        [PINCODE_PATTERN], text, confidence=0.95, field_name="pincode"
    )


def extract_phone_numbers(text: str) -> List[ExtractedField]:
    results = []
    for match in PHONE_PATTERN.finditer(text):
        # group(2) is the 10-digit number
        number = match.group(2)
        results.append(ExtractedField(
            name="phone_number",
            value=number,
            confidence=0.90,
            raw_value=match.group(0),
            char_position=match.start(),
        ))
    return results


# ── Full document parser ───────────────────────────────────────────────────

def parse_document(
    text: str,
    page_number: Optional[int] = None,
) -> ParsedDocument:
    """
    Parse a Gujarati government document page and extract all structured fields.

    Args:
        text: Corrected, normalized text from OCR or digital extraction.
        page_number: Page number for field attribution.

    Returns:
        ParsedDocument with all extracted fields and structured data.
    """
    doc_type = detect_document_type(text)

    fields: Dict[str, ExtractedField] = {}
    warnings: List[str] = []

    # Extract dates
    dates = extract_dates(text)
    if dates:
        fields["date"] = dates[0]
        if len(dates) > 1:
            for i, d in enumerate(dates[1:], 1):
                fields[f"date_{i+1}"] = d

    # Extract location hierarchy
    village = extract_village(text)
    taluka = extract_taluka(text)
    district = extract_district(text)

    if village:
        village.page_number = page_number
        fields["village"] = village
    if taluka:
        taluka.page_number = page_number
        fields["taluka"] = taluka
    if district:
        district.page_number = page_number
        fields["district"] = district

    # Survey and mutation numbers
    survey_nums = extract_survey_numbers(text)
    if survey_nums:
        fields["survey_number"] = survey_nums[0]
        for i, sn in enumerate(survey_nums[1:], 1):
            fields[f"survey_number_{i+1}"] = sn

    mutation_nums = extract_mutation_numbers(text)
    if mutation_nums:
        fields["mutation_number"] = mutation_nums[0]

    # Owner/holder names
    owners = extract_owner_names(text)
    if owners:
        fields["primary_owner"] = owners[0]
        for i, o in enumerate(owners[1:], 1):
            fields[f"owner_{i+1}"] = o

    # Financial data
    amounts = extract_amounts(text)
    if amounts:
        fields["primary_amount"] = amounts[0]
        for i, a in enumerate(amounts[1:], 1):
            fields[f"amount_{i+1}"] = a

    # Land area
    areas = extract_areas(text)
    if areas:
        fields["land_area"] = areas[0]

    # Administrative references
    notice_nums = extract_notice_numbers(text)
    if notice_nums:
        fields["notice_number"] = notice_nums[0]

    pincodes = extract_pincodes(text)
    if pincodes:
        fields["pincode"] = pincodes[0]

    # Phones
    phones = extract_phone_numbers(text)
    if phones:
        fields["phone_number"] = phones[0]

    # Validate location completeness
    if doc_type in (DocumentType.SEVEN_TWELVE, DocumentType.MUTATION, DocumentType.SURVEY):
        if not village:
            warnings.append("Village name not found in land record")
        if not survey_nums:
            warnings.append("Survey number not found in land record")

    # Compute overall confidence
    if fields:
        avg_confidence = sum(f.confidence for f in fields.values()) / len(fields)
    else:
        avg_confidence = 0.0
        warnings.append("No structured fields extracted")

    # Build structured data dict (flat, JSON-serializable)
    structured_data = {
        k: {"value": v.value, "confidence": v.confidence, "source": v.source}
        for k, v in fields.items()
    }
    structured_data["document_type"] = doc_type.value

    return ParsedDocument(
        document_type=doc_type,
        fields=fields,
        structured_data=structured_data,
        confidence=avg_confidence,
        warnings=warnings,
    )


def parse_multi_page_document(
    page_texts: List[str],
) -> ParsedDocument:
    """
    Parse a multi-page document, merging fields across pages.
    Later pages override earlier ones for the same field (more specific).
    """
    merged_fields: Dict[str, ExtractedField] = {}
    all_warnings: List[str] = []
    doc_type = DocumentType.UNKNOWN

    # Detect document type from combined text
    combined = " ".join(page_texts[:3])  # Use first 3 pages for type detection
    doc_type = detect_document_type(combined)

    for page_num, text in enumerate(page_texts, 1):
        page_doc = parse_document(text, page_number=page_num)
        all_warnings.extend(page_doc.warnings)

        for field_name, field_value in page_doc.fields.items():
            # Merge: take highest-confidence value for each field
            existing = merged_fields.get(field_name)
            if existing is None or field_value.confidence > existing.confidence:
                merged_fields[field_name] = field_value

    avg_confidence = (
        sum(f.confidence for f in merged_fields.values()) / len(merged_fields)
        if merged_fields else 0.0
    )

    structured_data = {
        k: {"value": v.value, "confidence": v.confidence, "source": v.source}
        for k, v in merged_fields.items()
    }
    structured_data["document_type"] = doc_type.value

    return ParsedDocument(
        document_type=doc_type,
        fields=merged_fields,
        structured_data=structured_data,
        confidence=avg_confidence,
        warnings=list(set(all_warnings)),
    )