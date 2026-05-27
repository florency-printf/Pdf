"""
Answer Generation Engine — Stage 9
=====================================
Generates reliable answers to user questions about Gujarati government documents.

Key principle: NEVER trust raw OCR blindly.
The engine uses:
  1. Validated structured fields (highest reliability)
  2. Corrected OCR text (medium reliability)
  3. Raw OCR text (last resort, flagged)

Answer types:
  - Field lookup ("What is the survey number?")
  - Presence check ("Does this document mention Gandhinagar?")
  - Summary ("Summarize this document")
  - Comparison ("Who are the owners?")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.document_parser import (
    DocumentType,
    ExtractedField,
    ParsedDocument,
)
from app.services.field_validator import ValidationReport


# ── Intent detection ───────────────────────────────────────────────────────

INTENT_PATTERNS = {
    "survey_number": [
        r"સ[ર]*[\.]*\s*ન[ં]*[\.]*",
        r"survey\s*num",
        r"survey\s*no",
        r"ભૂ[- ]*ન",
    ],
    "owner_name": [
        r"ખ[ા]?ત[ે]?દ[ા]?ર",
        r"ધ[ા]?ર[ણ]?ક",
        r"owner",
        r"ન[ા]?મ",
        r"name",
    ],
    "village": [
        r"ગ[ા]?મ",
        r"village",
    ],
    "taluka": [
        r"ત[ા]?ل",
        r"taluka",
        r"ત[ા]?લ",
    ],
    "district": [
        r"જિ[ë]*[ë]*ä[ë]*",
        r"district",
        r"જિ[લ]?",
    ],
    "date": [
        r"ત[ા]?\.",
        r"date",
        r"તારીખ",
        r"ક[્]?ય[ા]?ร",
    ],
    "amount": [
        r"₹|Rs|રૂ",
        r"amount",
        r"tax",
        r"વેરો",
        r"ટ[ે]?ક[્]?સ",
    ],
    "mutation_number": [
        r"ફ[ે]?ર.*?ન[ં]",
        r"mutation",
        r"ન[ં].*?ક[્]?ર",
    ],
    "document_type": [
        r"document type",
        r"what.*type",
        r"what.*kind",
        r"what is this",
    ],
    "summary": [
        r"summar",
        r"overview",
        r"brief",
        r"describe",
        r"about.*doc",
    ],
    "land_area": [
        r"area",
        r"size",
        r"land.*size",
        r"hectare",
        r"ગ[ુ]?ઠ",
        r"ક્ષ[ે]?ત[્]?ર",
    ],
    "pincode": [
        r"pin",
        r"postal",
        r"zip",
    ],
    "all_fields": [
        r"all\s+fields",
        r"extract\s+all",
        r"all\s+data",
        r"full\s+extraction",
    ],
}


def _detect_intent(question: str) -> List[str]:
    """Detect which fields the question is asking about."""
    question_lower = question.lower()
    matched = []
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, question_lower, re.IGNORECASE | re.UNICODE):
                matched.append(intent)
                break
    return matched or ["general"]


# ── Confidence level labels ────────────────────────────────────────────────

def _confidence_label(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.65:
        return "medium"
    if confidence >= 0.40:
        return "low"
    return "very_low"


# ── Answer dataclass ───────────────────────────────────────────────────────

@dataclass
class DocumentAnswer:
    question: str
    answer: str
    confidence: float
    confidence_label: str
    source: str                      # "validated_field", "corrected_text", "raw_text"
    supporting_fields: Dict[str, Any] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)


# ── Field answer builders ──────────────────────────────────────────────────

def _answer_from_field(
    field_name: str,
    doc: ParsedDocument,
    validation: Optional[ValidationReport] = None,
) -> Optional[DocumentAnswer]:
    """Build an answer from a validated extracted field."""
    extracted = doc.fields.get(field_name)
    if not extracted:
        return None

    # Use suggested correction from validation if available
    corrected_value = extracted.value
    confidence = extracted.confidence
    source = extracted.source

    if validation:
        fv = validation.field_validations.get(field_name)
        if fv:
            if fv.suggested_value:
                corrected_value = fv.suggested_value
                confidence = min(1.0, confidence + fv.confidence_adjustment)
                source = "validated_field"
            elif not fv.is_valid:
                confidence = max(0.0, confidence + fv.confidence_adjustment)

    return DocumentAnswer(
        question="",
        answer=corrected_value,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        source=source,
        supporting_fields={field_name: corrected_value},
    )


def _search_corrected_text(
    query: str,
    corrected_text: str,
    context_chars: int = 200,
) -> Optional[Tuple[str, float]]:
    """
    Search for query terms in the corrected text and return surrounding context.
    Returns (context_snippet, confidence) or None.
    """
    if not corrected_text:
        return None

    query_lower = query.lower()
    text_lower = corrected_text.lower()

    idx = text_lower.find(query_lower)
    if idx == -1:
        return None

    start = max(0, idx - context_chars // 2)
    end = min(len(corrected_text), idx + len(query) + context_chars // 2)
    snippet = corrected_text[start:end].strip()
    return snippet, 0.60   # Medium confidence for text search


# ── Document type descriptions ─────────────────────────────────────────────

DOCUMENT_TYPE_DESCRIPTIONS = {
    DocumentType.SEVEN_TWELVE: "7/12 Land Record Extract (સાત-બાર ઉતારો) — official record of land ownership, area, and crop details",
    DocumentType.MUTATION: "Mutation Entry (ફેરફાર નોંધ) — records change of ownership or land rights",
    DocumentType.SURVEY: "Survey Record (સર્વે) — land survey and measurement document",
    DocumentType.TAX_NOTICE: "Tax/Revenue Notice (કર નોટિસ) — demand for property or land tax payment",
    DocumentType.GRAM_PANCHAYAT: "Gram Panchayat Record (ગ્રામ પંચાયત) — village-level administrative document",
    DocumentType.GIDC_NOTICE: "GIDC Notice — Gujarat Industrial Development Corporation document",
    DocumentType.COURT_NOTICE: "Court/Legal Notice — judicial or quasi-judicial proceeding document",
    DocumentType.REVENUE: "Revenue Department Document — state revenue administration record",
    DocumentType.PROPERTY_CARD: "Property Card (મિલ્કત કાર્ડ) — urban property ownership record",
    DocumentType.UNKNOWN: "Document type could not be determined from available text",
}


# ── Summary builder ────────────────────────────────────────────────────────

def _build_summary(
    doc: ParsedDocument,
    validation: Optional[ValidationReport] = None,
) -> str:
    """Build a human-readable summary of the parsed document."""
    lines = []

    # Document type
    doc_desc = DOCUMENT_TYPE_DESCRIPTIONS.get(doc.document_type, "Government document")
    lines.append(f"Document type: {doc_desc}")

    # Location
    location_parts = []
    for loc_field in ["village", "taluka", "district"]:
        f = doc.fields.get(loc_field)
        if f:
            location_parts.append(f.value)
    if location_parts:
        lines.append(f"Location: {', '.join(location_parts)}")

    # Key identifiers
    if "survey_number" in doc.fields:
        lines.append(f"Survey No: {doc.fields['survey_number'].value}")
    if "mutation_number" in doc.fields:
        lines.append(f"Mutation No: {doc.fields['mutation_number'].value}")

    # Owners
    owners = [v.value for k, v in doc.fields.items() if "owner" in k]
    if owners:
        lines.append(f"Owner(s): {'; '.join(owners[:3])}")

    # Dates
    dates = [v.value for k, v in doc.fields.items() if k.startswith("date")]
    if dates:
        lines.append(f"Date(s): {', '.join(dates[:2])}")

    # Amounts
    amounts = [v.value for k, v in doc.fields.items() if "amount" in k]
    if amounts:
        lines.append(f"Amount(s): ₹ {', '.join(amounts[:2])}")

    # Land area
    if "land_area" in doc.fields:
        lines.append(f"Land area: {doc.fields['land_area'].value}")

    # Confidence
    conf = validation.validated_confidence if validation else doc.confidence
    lines.append(f"Extraction confidence: {conf:.0%} ({_confidence_label(conf)})")

    return "\n".join(lines)


# ── Main answer generation function ───────────────────────────────────────

def generate_answer(
    question: str,
    doc: ParsedDocument,
    corrected_text: str = "",
    validation: Optional[ValidationReport] = None,
) -> DocumentAnswer:
    """
    Generate a reliable answer to a question about a Gujarati government document.

    Priority chain:
      1. Validated structured field → highest reliability
      2. Structured field (unvalidated) → medium-high
      3. Search in corrected text → medium
      4. Return "not found" with caveats

    Args:
        question: User's question (English or Gujarati).
        doc: Parsed document with structured fields.
        corrected_text: Full corrected OCR/extraction text.
        validation: Optional validation report with suggested corrections.

    Returns:
        DocumentAnswer with answer, confidence, and source attribution.
    """
    intents = _detect_intent(question)

    caveats = []

    # Handle summary intent
    if "summary" in intents or "document_type" in intents:
        if "document_type" in intents:
            answer = DOCUMENT_TYPE_DESCRIPTIONS.get(
                doc.document_type,
                f"Unknown document type ({doc.document_type.value})"
            )
            return DocumentAnswer(
                question=question,
                answer=answer,
                confidence=0.80 if doc.document_type != DocumentType.UNKNOWN else 0.30,
                confidence_label=_confidence_label(0.80),
                source="structured_classification",
            )

        summary = _build_summary(doc, validation)
        return DocumentAnswer(
            question=question,
            answer=summary,
            confidence=doc.confidence,
            confidence_label=_confidence_label(doc.confidence),
            source="structured_summary",
        )

    # Handle "all fields" intent
    if "all_fields" in intents:
        sd = doc.structured_data
        if sd:
            lines = [f"{k}: {v['value']}" for k, v in sd.items() if isinstance(v, dict)]
            return DocumentAnswer(
                question=question,
                answer="\n".join(lines) if lines else "No structured fields extracted.",
                confidence=doc.confidence,
                confidence_label=_confidence_label(doc.confidence),
                source="structured_fields",
                supporting_fields=sd,
            )

    # Map intents to field names
    INTENT_TO_FIELD = {
        "survey_number": ["survey_number", "survey_number_2"],
        "owner_name": ["primary_owner", "owner_2", "owner_3"],
        "village": ["village"],
        "taluka": ["taluka"],
        "district": ["district"],
        "date": ["date", "date_2"],
        "amount": ["primary_amount", "amount_2"],
        "mutation_number": ["mutation_number"],
        "land_area": ["land_area"],
        "pincode": ["pincode"],
    }

    # Try structured fields first
    for intent in intents:
        field_names = INTENT_TO_FIELD.get(intent, [intent])
        for field_name in field_names:
            answer_obj = _answer_from_field(field_name, doc, validation)
            if answer_obj:
                answer_obj.question = question

                # Add caveat if confidence is low
                if answer_obj.confidence < 0.60:
                    caveats.append(
                        f"Low confidence ({answer_obj.confidence:.0%}) — "
                        "manual verification recommended"
                    )
                if validation and field_name in validation.field_validations:
                    fv = validation.field_validations[field_name]
                    if not fv.is_valid:
                        caveats.append(f"Validation issue: {'; '.join(fv.issues)}")
                    elif fv.issues:
                        caveats.append(f"Note: {fv.issues[0]}")

                answer_obj.caveats = caveats
                return answer_obj

    # Fall back to text search
    if corrected_text:
        # Try searching for relevant terms from the question
        search_terms = [t for t in question.split() if len(t) > 3]
        for term in search_terms:
            result = _search_corrected_text(term, corrected_text)
            if result:
                snippet, conf = result
                caveats.append("Answer extracted from document text, not structured field")
                return DocumentAnswer(
                    question=question,
                    answer=snippet,
                    confidence=conf,
                    confidence_label=_confidence_label(conf),
                    source="corrected_text_search",
                    caveats=caveats,
                )

    # Not found
    return DocumentAnswer(
        question=question,
        answer="Information not found in document.",
        confidence=0.0,
        confidence_label="very_low",
        source="not_found",
        caveats=[
            "The requested information could not be located in this document.",
            "Check if the document type matches expected content.",
        ],
    )


def batch_answer(
    questions: List[str],
    doc: ParsedDocument,
    corrected_text: str = "",
    validation: Optional[ValidationReport] = None,
) -> List[DocumentAnswer]:
    """Answer multiple questions about a document."""
    return [
        generate_answer(q, doc, corrected_text, validation)
        for q in questions
    ]


def answers_to_dict(answers: List[DocumentAnswer]) -> List[Dict[str, Any]]:
    """Serialize answers to JSON-safe dicts."""
    result = []
    for a in answers:
        result.append({
            "question": a.question,
            "answer": a.answer,
            "confidence": round(a.confidence, 3),
            "confidence_label": a.confidence_label,
            "source": a.source,
            "supporting_fields": a.supporting_fields,
            "caveats": a.caveats,
        })
    return result