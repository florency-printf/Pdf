"""
Gujarati Language Correction Engine — Stage 6
==============================================
Fixes OCR errors using:
  1. Known government vocabulary dictionary
  2. Gujarati district / taluka / village name dictionaries
  3. Legal terminology dictionary
  4. Fuzzy matching with edit distance
  5. Context-aware corrections (surrounding words inform disambiguation)

The engine is intentionally conservative: it only corrects when confidence
is high, to avoid introducing new errors.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple

# ── Gujarat Administrative Geography ──────────────────────────────────────

GUJARAT_DISTRICTS: Set[str] = {
    "અમદાવાદ", "સુરત", "વડોદરા", "રાજકોટ", "ભાવનગર",
    "જામનગર", "જૂનાગઢ", "ગાંધીનગર", "આણંદ", "ખેડા",
    "મહેસાણા", "પાટણ", "બનાસકાંઠા", "સાબરકાંઠા", "અરવલ્લી",
    "ગાંધીનગર", "દાહોદ", "પંચમહાલ", "વડોદરા", "છોટા ઉદેપુર",
    "નર્મદા", "ભરૂચ", "સૂરત", "ડાંગ", "નવસારી", "વલસાડ",
    "તાપી", "સૌરાષ્ટ્ર", "કચ્છ", "પોરબંદર", "દ્વારકા",
    "મોરબી", "સુરેન્દ્રનગર", "અમરેલી", "ગીર સોમનાથ",
    "બોટાદ", "ખેડા", "આणंद",
    # English transliterations also stored for mixed docs
    "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar",
    "Jamnagar", "Junagadh", "Gandhinagar", "Anand", "Kheda",
    "Mehsana", "Patan", "Banaskantha", "Sabarkantha",
    "Dahod", "Panchmahal", "Narmada", "Bharuch", "Navsari",
    "Valsad", "Tapi", "Kutch", "Porbandar", "Dwarka",
    "Morbi", "Surendranagar", "Amreli", "Botad",
}

GUJARAT_TALUKAS: Set[str] = {
    # Ahmedabad district
    "ધોળકા", "ધંધૂકા", "બાવળા", "સણંદ", "વિરામગામ",
    "ડેસાઇ", "દસ્ક્રોઈ", "ધોળકા",
    # Gandhinagar
    "ગાંધીનગર", "કલોલ", "દેગામ", "માણસા",
    # Rajkot
    "રાજકોટ", "ગોંડલ", "જેતપુર", "ઉપલેટા", "વિંછીયા",
    "ધોરાજી", "જામ કંડોરણા", "લોધિકા", "કોટડા",
    # Surat
    "ઓલપાડ", "માંગરોળ", "ઉમ્બરગામ", "ચોર્યાસી", "પલસાણા",
    # Add more as needed for production
    "Dholka", "Sanand", "Kalol", "Gondal", "Jetpur",
}

# ── Government & Legal Vocabulary ──────────────────────────────────────────

GOVERNMENT_VOCAB: Dict[str, str] = {
    # Common OCR corruptions → correct forms
    "ગધિનગર": "ગાંધીનગર",
    "ગાધીનગર": "ગાંધીનગર",
    "ગાઘીનગર": "ગાંધીનગર",
    "ગાડીનગર": "ગાંધીનગર",

    "તખદાલ": "તબદલ",
    "તખ઼દાલ": "તબદલ",
    "ત્ખ઼દ": "તબદ",

    "સ઼ર્વે": "સર્વે",
    "સ઼ર્વ": "સર્વ",
    "ષ઼ર્વે": "સર્વે",

    "ચ઼ીઠ઼ઠ઼ી": "ચિઠ્ઠી",
    "ચ઼ઠ઼ઠ઼ી": "ચિઠ્ઠી",

    "મ઼હ઼ેસ઼ૂલ": "મહેસૂલ",
    "મ઼હ઼ેસ઼ુ": "મહેસૂ",

    "ઝ઼ઝ઼ેર": "ઝઝેર",  # common stamp artifact

    "ત઼ાઃ": "તા.",   # date abbreviation
    "ત઼ા઼": "તા.",

    "ગ઼ ઼.ન઼.": "ગ.ન.",  # gram panchayat abbreviation
    "ગ઼ ઼ ઼ ઼": "ગ.ન.",

    # Revenue document terms
    "ખ઼ા઼ત઼ેદ઼ા઼ર": "ખાતેદાર",
    "ખ઼ાત઼ેદ": "ખાતેદ",
    "ખ઼ેડ઼ૂ": "ખેડૂ",
    "ખ઼ેડ઼ૂત": "ખેડૂત",

    "ભ઼ૂ઼-ન઼ોં": "ભૂ-નોં",
    "ભ઼ૂ": "ભૂ",

    "હ઼ક઼": "હક",
    "હ઼ક઼ક઼": "હક્ક",
    "હ઼ક઼્ક઼": "હક્ક",

    "ત઼ા઼ઃ": "તા.",
    "ત઼ાઃ": "તા.",
    "ત઼ા઼:": "તા.",

    # Survey number patterns
    "સ઼ ઼.ન઼.": "સ.નં.",
    "સ ઼.ન ઼.": "સ.નં.",
    "ર઼ ઼.ન઼.": "ર.નં.",  # registration number
}

LEGAL_VOCAB: Dict[str, str] = {
    # Court / notice terms
    "ત઼ાઃ઼ ઼ ઼": "તા.",
    "ત઼ ઼ ઼ ઼ ઼ ઼ ઼": "ત.",
    "ઓ઼ ઼ ઼ ઼ ઼ ઼ ઼": "ઓ.",

    "ન઼ ઼.ઓ઼.": "નં.ઓ.",
    "ન઼ ઼.ઓ઼ ઼": "નં.ઓ.",

    "ઠ઼ ઼ ઼ ઼ ઼ ઼ ઼.": "ઠ.",  # stamp-blurred abbreviation

    "અ઼ ઼ ઼ ઼ ઼ ઼ ઼": "અ.",  # common prefix in notices

    "ક઼ ઼.ઉ઼.": "ક.ઉ.",  # case reference format
    "ક઼ ઼. ઉ઼.": "ક. ઉ.",

    "ન઼ ઼ ઼ ઼ ઼ ઼ ઼": "ન.",  # namuno (form) prefix
    "ન઼ ઼ ઼ ઼ ઼": "ન.",

    "ય઼ ઼ ઼ ઼ ઼ ઼ ઼": "ય.",
    "ઇ઼ ઼ ઼ ઼ ઼ ઼ ઼": "ઈ.",

    "ધ઼ ઼ ઼ ઼ ઼ ઼ ઼.": "ધ.",

    # Revenue/mutation terminology
    "ફ઼ ઼ ઼ ઼ ઼": "ફ.",  # form prefix
    "ફ઼ ઼ ઼ ઼": "ફ.",
    "ફ઼ ઼ ઼": "ફ.",
    "ફ઼ ઼": "ફ.",

    "ઓ઼ ઼ ઼ ઼": "ઓ.",
    "ઓ઼ ઼ ઼": "ઓ.",
    "ઓ઼ ઼": "ઓ.",
    "ઓ઼": "ઓ.",

    # Common legal section references
    "ક઼ ઼ ઼ ઼ ઼ ઼ ઼ ": "ક.",
}

# ── Combined corrections lookup ────────────────────────────────────────────

ALL_CORRECTIONS: Dict[str, str] = {**GOVERNMENT_VOCAB, **LEGAL_VOCAB}


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy comparison (NFC, lowercase, strip spaces)."""
    text = unicodedata.normalize("NFC", text)
    return text.strip().lower()


def _edit_distance_ratio(a: str, b: str) -> float:
    """Return similarity ratio 0.0–1.0 using SequenceMatcher."""
    return SequenceMatcher(None, a, b).ratio()


@lru_cache(maxsize=2048)
def _find_closest_district(word: str, threshold: float = 0.75) -> Optional[str]:
    """Find the closest matching district name using fuzzy matching."""
    norm_word = _normalize_for_comparison(word)
    best_match: Optional[str] = None
    best_ratio = threshold

    for district in GUJARAT_DISTRICTS:
        ratio = _edit_distance_ratio(norm_word, _normalize_for_comparison(district))
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = district

    return best_match


@lru_cache(maxsize=2048)
def _find_closest_taluka(word: str, threshold: float = 0.78) -> Optional[str]:
    """Find the closest matching taluka name."""
    norm_word = _normalize_for_comparison(word)
    best_match: Optional[str] = None
    best_ratio = threshold

    for taluka in GUJARAT_TALUKAS:
        ratio = _edit_distance_ratio(norm_word, _normalize_for_comparison(taluka))
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = taluka

    return best_match


def _apply_direct_corrections(text: str) -> str:
    """Apply direct vocabulary corrections (exact match, longest-first)."""
    corrections = sorted(ALL_CORRECTIONS.items(), key=lambda x: len(x[0]), reverse=True)
    for wrong, correct in corrections:
        text = text.replace(wrong, correct)
    return text


def _correct_word_with_fuzzy(
    word: str,
    reference_set: Set[str],
    threshold: float = 0.80,
) -> Optional[str]:
    """
    Try to correct a word by fuzzy-matching against a reference set.
    Only returns a correction if above threshold (conservative).
    """
    norm_word = _normalize_for_comparison(word)
    if len(norm_word) < 3:
        return None  # Don't correct very short words

    best_match: Optional[str] = None
    best_ratio = threshold

    for candidate in reference_set:
        norm_cand = _normalize_for_comparison(candidate)
        ratio = _edit_distance_ratio(norm_word, norm_cand)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = candidate

    return best_match


def _tokenize_gujarati(text: str) -> List[str]:
    """Simple whitespace + punctuation tokenizer for Gujarati text."""
    # Split on whitespace and common punctuation, preserving tokens
    tokens = re.split(r"(\s+|[।॥,.;:!\?\(\)\[\]{}/\\\"\']+)", text)
    return tokens


def correct_gujarati_text(
    text: str,
    apply_fuzzy: bool = True,
    fuzzy_threshold: float = 0.82,
) -> Tuple[str, List[str]]:
    """
    Apply language correction to Gujarati text.

    Stages:
      1. Direct vocabulary corrections (exact known bad→good mappings)
      2. Fuzzy district name correction
      3. Fuzzy taluka name correction

    Args:
        text: Text to correct (already Unicode-normalized).
        apply_fuzzy: Whether to apply fuzzy matching (slower, more aggressive).
        fuzzy_threshold: Minimum similarity ratio for fuzzy corrections.

    Returns:
        (corrected_text, list_of_changes_made)
    """
    if not text:
        return text, []

    changes: List[str] = []

    # Stage 1: Direct corrections
    corrected = _apply_direct_corrections(text)
    if corrected != text:
        changes.append("direct_vocabulary_correction")

    if not apply_fuzzy:
        return corrected, changes

    # Stage 2 & 3: Per-word fuzzy correction
    tokens = _tokenize_gujarati(corrected)
    new_tokens = []

    for token in tokens:
        token_stripped = token.strip()

        # Only attempt correction on Gujarati words of reasonable length
        if len(token_stripped) < 3 or not any(
            "\u0A80" <= c <= "\u0AFF" for c in token_stripped
        ):
            new_tokens.append(token)
            continue

        # Check district names
        district_fix = _find_closest_district(token_stripped, fuzzy_threshold)
        if district_fix and district_fix != token_stripped:
            new_tokens.append(district_fix)
            changes.append(f"district_correction: {token_stripped!r} → {district_fix!r}")
            continue

        # Check taluka names
        taluka_fix = _find_closest_taluka(token_stripped, fuzzy_threshold)
        if taluka_fix and taluka_fix != token_stripped:
            new_tokens.append(taluka_fix)
            changes.append(f"taluka_correction: {token_stripped!r} → {taluka_fix!r}")
            continue

        new_tokens.append(token)

    return "".join(new_tokens), changes


def correct_pages(
    texts: List[str],
    apply_fuzzy: bool = True,
) -> Tuple[List[str], List[List[str]]]:
    """Correct a list of page texts. Returns (corrected_texts, all_changes)."""
    corrected_texts = []
    all_changes = []

    for text in texts:
        corrected, changes = correct_gujarati_text(text, apply_fuzzy=apply_fuzzy)
        corrected_texts.append(corrected)
        all_changes.append(changes)

    return corrected_texts, all_changes


# ── OCR digit/consonant disambiguation ────────────────────────────────────

def disambiguate_digit_consonant(
    text: str,
    context_window: int = 3,
) -> str:
    """
    Context-aware disambiguation of Gujarati digit↔consonant confusions.

    Rules:
    - ૭ (digit 7) in numeric context → keep as digit 7
    - ૭ in word context → likely ત (ta consonant)
    - ૬ (digit 6) in numeric context → keep as digit 6
    - ૬ in word context → likely બ (ba consonant)
    - ૨ (digit 2) in numeric context → keep as digit 2
    - ૨ in word context surrounded by Gujarati consonants → ર (ra)
    """
    tokens = _tokenize_gujarati(text)
    result = []

    for i, token in enumerate(tokens):
        if not token.strip():
            result.append(token)
            continue

        # Check surrounding context
        prev_token = tokens[i - 1].strip() if i > 0 else ""
        next_token = tokens[i + 1].strip() if i < len(tokens) - 1 else ""

        # Is this token in numeric context?
        surrounding = prev_token + next_token
        has_digit_context = any(c.isdigit() for c in surrounding)
        has_guj_context = any("\u0A95" <= c <= "\u0ABA" for c in surrounding)

        new_token = token

        if has_guj_context and not has_digit_context:
            # Word context: replace digit lookalikes with consonants
            new_token = new_token.replace("૭", "ત")
            new_token = new_token.replace("૬", "બ")
            new_token = new_token.replace("૨", "ર")
        elif has_digit_context and not has_guj_context:
            # Pure numeric context: keep digits as-is
            pass
        else:
            # Mixed context: apply within-word heuristic
            # If the token itself contains Gujarati consonants, digits are likely misreads
            if any("\u0A95" <= c <= "\u0ABA" for c in new_token):
                new_token = new_token.replace("૭", "ત")
                new_token = new_token.replace("૨", "ર")
                # ૬ is more ambiguous — only replace if surrounded by Gujarati chars
                chars = list(new_token)
                for j, ch in enumerate(chars):
                    if ch == "૬":
                        left_is_guj = j > 0 and "\u0A80" <= chars[j-1] <= "\u0AFF"
                        right_is_guj = j < len(chars)-1 and "\u0A80" <= chars[j+1] <= "\u0AFF"
                        if left_is_guj and right_is_guj:
                            chars[j] = "બ"
                new_token = "".join(chars)

        result.append(new_token)

    return "".join(result)