"""
Gujarati text intelligence helpers.

This module provides three production-facing layers:
1. Unicode cleanup and conservative Gujarati text repair
2. Dictionary-aware correction for common government-document terms
3. Structured field extraction for downstream validation and QA

The functions are intentionally conservative so they improve noisy Gujarati
government PDFs without aggressively rewriting legitimate English or numeric
content.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import get_close_matches
from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

_CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f\u200b\u200c\u200d\u2060\ufeff]")
_TOKEN_RE = re.compile(r"[\u0A80-\u0AFF]+|[A-Za-z0-9_]+|\s+|[^\w\s]+", re.UNICODE)
_GUJARATI_RE = re.compile(r"[\u0A80-\u0AFF]")
_GUJARATI_DIGIT_TRANS = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")

# Exact, high-confidence repairs observed in Gujarati government scans.
_EXACT_CORRECTIONS = {
    "ગધિનગર": "ગાંધીનગર",
    "તખદાલાથા": "તબદલાથી",
}

# Conservative phrase repairs for the common broken-font cases described in the
# repo samples. These are applied before token-level fuzzy matching.
_PHRASE_REPAIRS: Tuple[Tuple[str, str], ...] = (
    ("ઓો", "આ"),
)

# Known governance / location vocabulary used for fuzzy correction.
_VOCABULARY: Tuple[str, ...] = (
    "ગાંધીનગર",
    "અમદાવાદ",
    "અમરેલી",
    "આણંદ",
    "અરવલ્લી",
    "બનાસકાંઠા",
    "ભરૂચ",
    "ભાવનગર",
    "દાહોદ",
    "દેવભૂમિ",
    "દ્વારકા",
    "ગીર",
    "જૂનાગઢ",
    "જામનગર",
    "કચ્છ",
    "ખેડા",
    "મહીસાગર",
    "મહેસાણા",
    "મોરબી",
    "નર્મદા",
    "નડિયાદ",
    "પંચમહાલ",
    "પાટણ",
    "રાજકોટ",
    "સાબરકાંઠા",
    "સુરત",
    "સુરેન્દ્રનગર",
    "તાપી",
    "વડોદરા",
    "વલસાડ",
    "જિલ્લો",
    "તાલુકો",
    "ગામ",
    "સર્વે",
    "સર્વેનં",
    "મ્યુટેશન",
    "નોટિસ",
    "કલમ",
    "કચેરી",
    "મામલતદાર",
    "મહેસૂલ",
    "રેવન્યુ",
    "પ્રમાણપત્ર",
    "જાહેરનામું",
    "હુકમ",
    "આદેશ",
    "અરજી",
    "કર",
    "વેરા",
)


def normalize_gujarati_text(text: str) -> str:
    """
    Apply Unicode NFC normalization and remove hidden control characters.

    This does not attempt semantic correction. It is safe to run on any text.
    """
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_RE.sub("", text)
    return text


def _apply_phrase_repairs(text: str) -> str:
    for src, dst in _PHRASE_REPAIRS:
        text = text.replace(src, dst)
    return text


def _looks_gujarati(token: str) -> bool:
    return bool(token and _GUJARATI_RE.search(token))


def _token_similarity(token: str, vocabulary: Sequence[str]) -> str:
    if not token or len(token) < 4 or not _looks_gujarati(token):
        return token

    # get_close_matches is conservative and works well for short Gujarati words
    # when the OCR error is small (e.g. missing matra or swapped glyph).
    matches = get_close_matches(token, vocabulary, n=1, cutoff=0.88)
    if matches:
        return matches[0]
    return token


def correct_gujarati_token(token: str) -> str:
    """
    Correct a single token if it looks like a common Gujarati OCR corruption.
    """
    if not token:
        return token

    if token in _EXACT_CORRECTIONS:
        return _EXACT_CORRECTIONS[token]

    if _looks_gujarati(token):
        return _token_similarity(token, _VOCABULARY)

    return token


def correct_gujarati_text(text: str) -> str:
    """
    Conservative Gujarati text repair:
    - Unicode cleanup
    - phrase-level fixes
    - token-level dictionary correction

    The function intentionally avoids rewriting numbers, punctuation, and
    English text.
    """
    if not text:
        return ""

    text = normalize_gujarati_text(text)
    text = _apply_phrase_repairs(text)

    parts: List[str] = []
    for piece in _TOKEN_RE.findall(text):
        if piece.isspace() or not piece:
            parts.append(piece)
            continue
        if _looks_gujarati(piece):
            parts.append(correct_gujarati_token(piece))
        else:
            parts.append(piece)

    return "".join(parts)


def _normalize_digits(text: str) -> str:
    return text.translate(_GUJARATI_DIGIT_TRANS)


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_field_value(value: str) -> str:
    value = normalize_gujarati_text(value)
    value = _normalize_digits(value)
    value = value.strip(" \t\r\n:;-.,")
    return value


def _extract_located_value(
    text: str,
    labels: Sequence[str],
) -> List[str]:
    values: List[str] = []
    for label in labels:
        pattern = rf"(?:^|[\s,;:។\-])(?:{label})\s*[:\-]?\s*([^\n,;]+)"
        for match in re.finditer(
            pattern, text, flags=re.IGNORECASE | re.UNICODE | re.MULTILINE
        ):
            value = _normalize_field_value(match.group(1))
            if value:
                values.append(value)
    return _dedupe_preserve_order(values)


def _extract_keyword_number(
    text: str,
    labels: Sequence[str],
) -> List[str]:
    values: List[str] = []
    label_pattern = "|".join(labels)
    pattern = rf"(?:{label_pattern})\s*(?:નં\.?|નંબર|No\.?|Number)?\s*[:\-]?\s*([0-9૦-૯A-Za-z\/\-.]+)"
    for match in re.finditer(
        pattern, text, flags=re.IGNORECASE | re.UNICODE | re.MULTILINE
    ):
        value = _normalize_field_value(match.group(1))
        if value:
            values.append(value)
    return _dedupe_preserve_order(values)


def _extract_dates(text: str) -> List[str]:
    raw_dates = []
    patterns = [
        r"(?:તા\.?|તારીખ|Date)\s*[:\-]?\s*([0-9૦-૯]{1,2}[\/\-.][0-9૦-૯]{1,2}[\/\-.][0-9૦-૯]{2,4})",
        r"\b([0-9૦-૯]{1,2}[\/\-.][0-9૦-૯]{1,2}[\/\-.][0-9૦-૯]{2,4})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(
            pattern, text, flags=re.IGNORECASE | re.UNICODE | re.MULTILINE
        ):
            raw_dates.append(_normalize_field_value(match.group(1)))

    valid_dates = []
    for raw in _dedupe_preserve_order(raw_dates):
        try:
            normalized = _normalize_digits(raw)
            parts = re.split(r"[\/\-.]", normalized)
            if len(parts) != 3:
                continue
            day, month, year = (int(p) for p in parts)
            if year < 100:
                year += 2000 if year < 50 else 1900
            datetime(year, month, day)
            valid_dates.append(f"{day:02d}-{month:02d}-{year:04d}")
        except Exception:
            continue
    return valid_dates


def _confidence_from_fields(fields: Dict[str, Any]) -> float:
    score = 0.0
    score += 0.18 if fields.get("dates") else 0.0
    score += 0.18 if fields.get("survey_numbers") else 0.0
    score += 0.14 if fields.get("notice_numbers") else 0.0
    score += 0.14 if fields.get("mutation_numbers") else 0.0
    locations = fields.get("locations", {})
    score += 0.16 if locations.get("districts") else 0.0
    score += 0.10 if locations.get("talukas") else 0.0
    score += 0.10 if locations.get("villages") else 0.0
    return round(min(score, 1.0), 4)


def extract_structured_fields(text: str) -> Dict[str, Any]:
    """
    Extract common Gujarati government document fields from the corrected text.
    """
    if not text:
        return {
            "dates": [],
            "survey_numbers": [],
            "mutation_numbers": [],
            "notice_numbers": [],
            "registration_numbers": [],
            "legal_sections": [],
            "amounts": [],
            "locations": {
                "districts": [],
                "talukas": [],
                "villages": [],
            },
            "confidence": 0.0,
        }

    normalized = normalize_gujarati_text(text)
    normalized = _normalize_digits(normalized)

    dates = _extract_dates(normalized)
    survey_numbers = _extract_keyword_number(normalized, [r"સર્વે", r"Survey"])
    mutation_numbers = _extract_keyword_number(
        normalized, [r"મ્યુટેશન", r"Mutation"]
    )
    notice_numbers = _extract_keyword_number(normalized, [r"નોટિસ", r"Notice"])
    registration_numbers = _extract_keyword_number(
        normalized, [r"નોંધણી", r"Registration", r"રજીસ્ટ્રેશન"]
    )
    legal_sections = _extract_keyword_number(normalized, [r"કલમ", r"Section"])

    amount_pattern = r"(?:રૂ\.?|Rs\.?|₹)\s*([0-9,]+(?:\.[0-9]+)?)"
    amounts = _dedupe_preserve_order(
        _normalize_field_value(m.group(1))
        for m in re.finditer(
            amount_pattern,
            normalized,
            flags=re.IGNORECASE | re.UNICODE | re.MULTILINE,
        )
    )

    locations = {
        "districts": _extract_located_value(normalized, [r"જિલ્લો", r"District"]),
        "talukas": _extract_located_value(normalized, [r"તાલુકો", r"Taluka"]),
        "villages": _extract_located_value(normalized, [r"ગામ", r"Village"]),
    }

    fields: Dict[str, Any] = {
        "dates": dates,
        "survey_numbers": survey_numbers,
        "mutation_numbers": mutation_numbers,
        "notice_numbers": notice_numbers,
        "registration_numbers": registration_numbers,
        "legal_sections": legal_sections,
        "amounts": amounts,
        "locations": locations,
    }
    fields["confidence"] = _confidence_from_fields(fields)
    return fields


def validate_structured_fields(fields: Dict[str, Any]) -> List[str]:
    """
    Validate the extracted structured fields and return a list of issues.
    """
    issues: List[str] = []

    if not fields.get("dates"):
        issues.append("No valid dates extracted.")
    if not fields.get("survey_numbers") and not fields.get("notice_numbers"):
        issues.append("No survey or notice number extracted.")

    locations = fields.get("locations", {})
    if not locations.get("districts"):
        issues.append("No district detected.")

    return issues


__all__ = [
    "correct_gujarati_text",
    "correct_gujarati_token",
    "extract_structured_fields",
    "normalize_gujarati_text",
    "validate_structured_fields",
]
