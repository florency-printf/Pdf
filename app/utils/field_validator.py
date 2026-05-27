"""
Field Validation Engine — Stage 8
===================================
Validates extracted fields using:
  - Known district/taluka/village name lists
  - Date format and range validation
  - Survey number format validation
  - Amount sanity checks
  - PIN code range validation for Gujarat
  - Cross-field consistency checks

The validation engine produces a ValidationReport per document.
Fields that fail validation are flagged, not silently discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.document_parser import ExtractedField, ParsedDocument, DocumentType
from app.services.gujarati_correction import (
    GUJARAT_DISTRICTS,
    GUJARAT_TALUKAS,
    _normalize_for_comparison,
    _edit_distance_ratio,
)

# ── Known valid patterns ───────────────────────────────────────────────────

# Gujarat PIN codes: 360000–396999
GUJARAT_PIN_MIN = 360000
GUJARAT_PIN_MAX = 396999

# Survey number: digits, optionally with / or - for sub-division
SURVEY_NUMBER_RE = re.compile(r"^\d{1,6}(?:[/\-]\d{1,4})?(?:[/\-]\d{1,4})?$")

# Mutation number: digits, optionally year-suffixed
MUTATION_NUMBER_RE = re.compile(r"^\d{1,6}(?:/\d{2,4})?$")

# Notice/registration number: alphanumeric with slashes
NOTICE_NUMBER_RE = re.compile(r"^[A-Za-z0-9/\-]{1,30}$")

# Date range: government records typically 1900–2030
DATE_MIN_YEAR = 1900
DATE_MAX_YEAR = 2030

# Reasonable land area upper bounds (Gujarat context)
MAX_AREA_HECTARES = 10000.0
MAX_AREA_GUNTHA = 40.0   # 40 guntha = 1 acre
MAX_AMOUNT_INR = 1_000_000_000.0   # 100 crore — sanity cap

# Fuzzy threshold for district/taluka name validation
LOCATION_FUZZY_THRESHOLD = 0.72


@dataclass
class FieldValidation:
    field_name: str
    is_valid: bool
    issues: List[str] = field(default_factory=list)
    suggested_value: Optional[str] = None
    confidence_adjustment: float = 0.0   # Added to field confidence


@dataclass
class ValidationReport:
    overall_valid: bool
    field_validations: Dict[str, FieldValidation] = field(default_factory=dict)
    cross_field_issues: List[str] = field(default_factory=list)
    validated_confidence: float = 0.0
    summary: str = ""


# ── Individual field validators ────────────────────────────────────────────

def validate_district(value: str) -> FieldValidation:
    """Validate district name against known Gujarat districts."""
    norm = _normalize_for_comparison(value)

    # Exact match
    for district in GUJARAT_DISTRICTS:
        if _normalize_for_comparison(district) == norm:
            return FieldValidation(
                field_name="district",
                is_valid=True,
                confidence_adjustment=0.10,
            )

    # Fuzzy match
    best_match: Optional[str] = None
    best_ratio = LOCATION_FUZZY_THRESHOLD
    for district in GUJARAT_DISTRICTS:
        ratio = _edit_distance_ratio(norm, _normalize_for_comparison(district))
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = district

    if best_match:
        return FieldValidation(
            field_name="district",
            is_valid=True,
            issues=[f"District fuzzy-matched: '{value}' → '{best_match}' (ratio={best_ratio:.2f})"],
            suggested_value=best_match,
            confidence_adjustment=0.05,
        )

    return FieldValidation(
        field_name="district",
        is_valid=False,
        issues=[f"District '{value}' not found in Gujarat district list"],
        confidence_adjustment=-0.15,
    )


def validate_taluka(value: str) -> FieldValidation:
    """Validate taluka name against known Gujarat talukas."""
    norm = _normalize_for_comparison(value)

    for taluka in GUJARAT_TALUKAS:
        if _normalize_for_comparison(taluka) == norm:
            return FieldValidation(
                field_name="taluka",
                is_valid=True,
                confidence_adjustment=0.08,
            )

    best_match: Optional[str] = None
    best_ratio = LOCATION_FUZZY_THRESHOLD
    for taluka in GUJARAT_TALUKAS:
        ratio = _edit_distance_ratio(norm, _normalize_for_comparison(taluka))
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = taluka

    if best_match:
        return FieldValidation(
            field_name="taluka",
            is_valid=True,
            issues=[f"Taluka fuzzy-matched: '{value}' → '{best_match}'"],
            suggested_value=best_match,
            confidence_adjustment=0.04,
        )

    # Talukas list is incomplete — don't penalize unknown talukas harshly
    return FieldValidation(
        field_name="taluka",
        is_valid=True,   # Accept but flag
        issues=[f"Taluka '{value}' not in known list (may be valid — list incomplete)"],
        confidence_adjustment=-0.05,
    )


def validate_date(value: str) -> FieldValidation:
    """Validate date string."""
    # Try multiple formats
    date_formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y",
    ]

    parsed_date: Optional[date] = None
    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(value.strip(), fmt).date()
            break
        except ValueError:
            continue

    if parsed_date is None:
        return FieldValidation(
            field_name="date",
            is_valid=False,
            issues=[f"Cannot parse date: '{value}'"],
            confidence_adjustment=-0.20,
        )

    if not (DATE_MIN_YEAR <= parsed_date.year <= DATE_MAX_YEAR):
        return FieldValidation(
            field_name="date",
            is_valid=False,
            issues=[f"Date year {parsed_date.year} outside valid range {DATE_MIN_YEAR}–{DATE_MAX_YEAR}"],
            confidence_adjustment=-0.15,
        )

    # Future dates are suspicious for land records (not invalid, but flagged)
    if parsed_date > date.today():
        return FieldValidation(
            field_name="date",
            is_valid=True,
            issues=["Date is in the future — may be a notice/deadline date"],
            confidence_adjustment=0.0,
        )

    return FieldValidation(
        field_name="date",
        is_valid=True,
        confidence_adjustment=0.05,
    )


def validate_survey_number(value: str) -> FieldValidation:
    """Validate survey number format."""
    cleaned = value.strip().replace(" ", "")
    if SURVEY_NUMBER_RE.match(cleaned):
        # Range check: survey numbers are typically < 99999
        parts = re.split(r"[/\-]", cleaned)
        if parts and int(parts[0]) > 99999:
            return FieldValidation(
                field_name="survey_number",
                is_valid=False,
                issues=[f"Survey number {parts[0]} suspiciously large (> 99999)"],
                confidence_adjustment=-0.10,
            )
        return FieldValidation(
            field_name="survey_number",
            is_valid=True,
            confidence_adjustment=0.08,
        )

    return FieldValidation(
        field_name="survey_number",
        is_valid=False,
        issues=[f"Survey number '{value}' does not match expected format (digits[/digits])"],
        confidence_adjustment=-0.15,
    )


def validate_mutation_number(value: str) -> FieldValidation:
    """Validate mutation entry number."""
    cleaned = value.strip().replace(" ", "")
    if MUTATION_NUMBER_RE.match(cleaned):
        return FieldValidation(
            field_name="mutation_number",
            is_valid=True,
            confidence_adjustment=0.06,
        )
    return FieldValidation(
        field_name="mutation_number",
        is_valid=False,
        issues=[f"Mutation number '{value}' format invalid"],
        confidence_adjustment=-0.10,
    )


def validate_amount(value: str) -> FieldValidation:
    """Validate monetary amount."""
    # Remove commas and parse
    cleaned = value.replace(",", "").replace("/-", "").strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return FieldValidation(
            field_name="amount",
            is_valid=False,
            issues=[f"Cannot parse amount: '{value}'"],
            confidence_adjustment=-0.20,
        )

    if amount < 0:
        return FieldValidation(
            field_name="amount",
            is_valid=False,
            issues=["Negative amount"],
            confidence_adjustment=-0.25,
        )

    if amount > MAX_AMOUNT_INR:
        return FieldValidation(
            field_name="amount",
            is_valid=True,
            issues=[f"Amount {amount:.0f} is very large — verify extraction"],
            confidence_adjustment=-0.05,
        )

    return FieldValidation(
        field_name="amount",
        is_valid=True,
        confidence_adjustment=0.05,
    )


def validate_pincode(value: str) -> FieldValidation:
    """Validate Gujarat PIN code."""
    cleaned = value.strip()
    try:
        pin = int(cleaned)
    except ValueError:
        return FieldValidation(
            field_name="pincode",
            is_valid=False,
            issues=[f"PIN code '{value}' is not numeric"],
            confidence_adjustment=-0.20,
        )

    if GUJARAT_PIN_MIN <= pin <= GUJARAT_PIN_MAX:
        return FieldValidation(
            field_name="pincode",
            is_valid=True,
            confidence_adjustment=0.10,
        )

    return FieldValidation(
        field_name="pincode",
        is_valid=False,
        issues=[f"PIN code {pin} is outside Gujarat range ({GUJARAT_PIN_MIN}–{GUJARAT_PIN_MAX})"],
        confidence_adjustment=-0.15,
    )


# ── Dispatch table ─────────────────────────────────────────────────────────

_FIELD_VALIDATORS = {
    "district":        validate_district,
    "taluka":          validate_taluka,
    "date":            validate_date,
    "date_2":          validate_date,
    "date_3":          validate_date,
    "survey_number":   validate_survey_number,
    "mutation_number": validate_mutation_number,
    "primary_amount":  validate_amount,
    "pincode":         validate_pincode,
}


# ── Cross-field consistency checks ────────────────────────────────────────

def _check_location_consistency(
    fields: Dict[str, ExtractedField],
) -> List[str]:
    """
    Verify that district and taluka are consistent with each other.
    (Simple check: if both present, taluka must plausibly belong to district.)
    """
    issues = []
    district_field = fields.get("district")
    taluka_field = fields.get("taluka")

    if district_field and taluka_field:
        # If district is known to not contain this taluka, flag it
        # (Full mapping would require a complete geographic DB — we use simple heuristics)
        dist_val = district_field.value.strip()
        tal_val = taluka_field.value.strip()

        # If they're identical (OCR merged them), flag
        if _normalize_for_comparison(dist_val) == _normalize_for_comparison(tal_val):
            issues.append(
                f"District and taluka have same value '{dist_val}' — possible OCR merge"
            )

    return issues


def _check_date_ordering(fields: Dict[str, ExtractedField]) -> List[str]:
    """Check that multiple dates are in reasonable order."""
    issues = []
    date_fields = [(k, v) for k, v in fields.items() if k.startswith("date")]

    dates = []
    for field_name, f in date_fields:
        for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"]:
            try:
                d = datetime.strptime(f.value.strip(), fmt).date()
                dates.append((field_name, d))
                break
            except ValueError:
                pass

    if len(dates) >= 2:
        dates.sort(key=lambda x: x[1])
        # If earliest date is > 50 years before latest, flag
        span = (dates[-1][1] - dates[0][1]).days
        if span > 365 * 50:
            issues.append(
                f"Date span of {span // 365} years between {dates[0][0]} and {dates[-1][0]} — verify"
            )

    return issues


# ── Main validation function ───────────────────────────────────────────────

def validate_parsed_document(doc: ParsedDocument) -> ValidationReport:
    """
    Validate all fields in a ParsedDocument.

    Returns a ValidationReport with per-field results and adjusted confidence.
    """
    field_validations: Dict[str, FieldValidation] = {}
    total_adjustment = 0.0
    n_validated = 0
    all_valid = True

    # Per-field validation
    for field_name, extracted_field in doc.fields.items():
        validator = _FIELD_VALIDATORS.get(field_name)
        if validator is None:
            # No validator for this field — accept as-is
            continue

        validation = validator(extracted_field.value)
        validation.field_name = field_name
        field_validations[field_name] = validation

        total_adjustment += validation.confidence_adjustment
        n_validated += 1

        if not validation.is_valid:
            all_valid = False

    # Cross-field checks
    cross_issues = []
    cross_issues.extend(_check_location_consistency(doc.fields))
    cross_issues.extend(_check_date_ordering(doc.fields))

    if cross_issues:
        all_valid = False

    # Apply confidence adjustments
    base_confidence = doc.confidence
    if n_validated > 0:
        avg_adjustment = total_adjustment / n_validated
        validated_confidence = max(0.0, min(1.0, base_confidence + avg_adjustment))
    else:
        validated_confidence = base_confidence

    # Build summary
    invalid_fields = [k for k, v in field_validations.items() if not v.is_valid]
    if invalid_fields:
        summary = f"Validation failed for: {', '.join(invalid_fields)}"
    elif cross_issues:
        summary = f"Cross-field issues: {'; '.join(cross_issues[:2])}"
    else:
        summary = f"All {n_validated} fields validated successfully"

    return ValidationReport(
        overall_valid=all_valid and not cross_issues,
        field_validations=field_validations,
        cross_field_issues=cross_issues,
        validated_confidence=validated_confidence,
        summary=summary,
    )


def apply_suggested_corrections(
    doc: ParsedDocument,
    validation: ValidationReport,
) -> ParsedDocument:
    """
    Apply suggested corrections from validation (e.g. fuzzy-matched names).
    Returns a new ParsedDocument with corrections applied.
    """
    updated_fields = dict(doc.fields)
    updated_structured = dict(doc.structured_data)

    for field_name, fv in validation.field_validations.items():
        if fv.suggested_value and field_name in updated_fields:
            original = updated_fields[field_name]
            updated_fields[field_name] = ExtractedField(
                name=original.name,
                value=fv.suggested_value,
                confidence=min(1.0, original.confidence + fv.confidence_adjustment),
                source="validated",
                raw_value=original.raw_value,
                page_number=original.page_number,
            )
            updated_structured[field_name] = {
                "value": fv.suggested_value,
                "confidence": updated_fields[field_name].confidence,
                "source": "validated",
            }

    return ParsedDocument(
        document_type=doc.document_type,
        fields=updated_fields,
        structured_data=updated_structured,
        tables=doc.tables,
        confidence=validation.validated_confidence,
        warnings=doc.warnings + validation.cross_field_issues,
    )