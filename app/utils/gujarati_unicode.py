"""
Gujarati Unicode Normalization Engine — Stage 5
================================================
Fixes malformed Unicode produced by:
  - broken ToUnicode cmap in legacy PDFs
  - glyph-order corruption
  - legacy Gujarati font encodings (ISM, Shree-Guj, etc.)
  - OCR character confusions

Pipeline:
  1. Strip zero-width / control characters
  2. NFC normalization
  3. Fix malformed matra sequences
  4. Apply known corruption repair maps
  5. Fix vowel sign ordering (post-base matras before base consonants)
  6. Remove duplicate vowel signs
  7. Fix chandrabindu / anusvara misplacements
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Tuple

# ── Gujarati Unicode ranges ────────────────────────────────────────────────
# Block: U+0A80–U+0AFF
GUJ_CONSONANTS = set(range(0x0A95, 0x0ABB))   # ક–ળ
GUJ_VOWELS     = set(range(0x0A85, 0x0A94 + 1))  # અ–ઔ
GUJ_MATRAS     = set(range(0x0ABE, 0x0ACD + 1))  # ા–્
GUJ_DIGITS     = set(range(0x0AE6, 0x0AEF + 1))  # ૦–૯

# Virama (halant)
VIRAMA = "\u0ACD"
# Anusvara (nasal dot above)
ANUSVARA = "\u0A82"
# Visarga
VISARGA = "\u0A83"
# Nukta
NUKTA = "\u0ABC"
# Chandrabindu
CHANDRABINDU = "\u0A81"

# Zero-width characters to strip
_ZERO_WIDTH = re.compile(
    r"[\u200b\u200c\u200d\u00ad\ufeff\u2028\u2029\u202a-\u202e\u2060-\u206f]"
)

# ── Known corruption repair maps ───────────────────────────────────────────
# These are common patterns from ISM/Shree font corruption.
# Format: (corrupted_pattern, correct_unicode)
CORRUPTION_REPAIRS: List[Tuple[str, str]] = [
    # Duplicate vowel signs
    ("\u0ABE\u0ABE", "\u0ABE"),      # ાા → ા
    ("\u0ABF\u0ABF", "\u0ABF"),      # િિ → િ
    ("\u0AC0\u0AC0", "\u0AC0"),      # ીી → ી
    ("\u0AC1\u0AC1", "\u0AC1"),      # ુુ → ુ
    ("\u0AC2\u0AC2", "\u0AC2"),      # ૂૂ → ૂ
    ("\u0AC7\u0AC7", "\u0AC7"),      # ેે → ે
    ("\u0AC8\u0AC8", "\u0AC8"),      # ૈૈ → ૈ
    ("\u0ACB\u0ACB", "\u0ACB"),      # ોો → ો
    ("\u0ACC\u0ACC", "\u0ACC"),      # ૌૌ → ૌ

    # Common misencoded vowel combinations → correct vowel signs
    ("\u0A93\u0ABE", "\u0A93"),      # ઓ + ા → ઓ (standalone)
    ("\u0A93\u0ACB", "\u0A93"),      # ઓ + ો → ઓ
    ("\u0A94\u0ACC", "\u0A94"),      # ઔ + ૌ → ઔ

    # ISM-style: standalone vowel + matra → proper dependent vowel
    ("\u0A87\u0ABF", "\u0ABF"),      # ઇ + િ → િ (was double-encoded)
    ("\u0A88\u0AC0", "\u0AC0"),      # ઈ + ી → ી
    ("\u0A89\u0AC1", "\u0AC1"),      # ઉ + ુ → ુ
    ("\u0A8A\u0AC2", "\u0AC2"),      # ઊ + ૂ → ૂ

    # Anusvara misplaced before consonant (should follow vowel)
    ("\u0A82\u0ABE", "\u0ABE\u0A82"),  # ં + ા → ા + ં

    # Chandrabindu duplicates
    ("\u0A81\u0A81", "\u0A81"),

    # Incorrect anusvara+virama sequences
    ("\u0A82\u0ACD", "\u0ACD"),      # ં + ् → ् (anusvara before virama is wrong)

    # Legacy font: e-matra (ે) encoded as a-matra (ા) + extra char
    # These are heuristic — real corruption is font-specific
    ("\u0A47\u0ABE", "\u0AC7"),      # incorrect e+aa → e-matra
]

# ── Regex for valid Gujarati syllable structure ────────────────────────────
# Consonant (+ optional virama) + optional matra + optional anusvara/visarga
_VALID_SYLLABLE = re.compile(
    r"[\u0A95-\u0ABA\u0ABD]"         # consonant
    r"(?:\u0ACD[\u0A95-\u0ABA])*"    # optional consonant clusters via virama
    r"[\u0ABE-\u0ACC]?"              # optional vowel matra
    r"[\u0A81-\u0A83]?",             # optional anusvara/chandrabindu/visarga
    re.UNICODE,
)

# ── Matra reordering: some fonts place matras before the consonant ─────────
# Pre-base matras that should appear after base consonant in Unicode order
_PREBASE_MATRA_RE = re.compile(
    r"([\u0ABF])([\u0A95-\u0ABA\u0ABD])",  # િ + consonant → consonant + િ
    re.UNICODE,
)

# ── OCR character confusion repair ─────────────────────────────────────────
OCR_CHAR_REPAIRS: Dict[str, str] = {
    # Digit-consonant confusions
    "૭": "ત",   # digit 7 ↔ ta
    "૬": "બ",   # digit 6 ↔ ba
    "૨": "ર",   # digit 2 ↔ ra (context-dependent — handled carefully)

    # Similar-glyph consonant confusions — only safe unambiguous ones
    "ધ": "દ",   # dha ↔ da (common OCR flip — context-dependent)
    "ભ": "મ",   # bha ↔ ma (less common)

    # Punctuation OCR artifacts
    "\u00a0": " ",   # non-breaking space → regular space
    "\u2013": "-",   # en dash → hyphen
    "\u2014": "-",   # em dash → hyphen
    "\u201c": '"',   # smart quotes
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
}

# Character confusions that are context-dependent (not applied blindly)
CONTEXT_DEPENDENT_REPAIRS: Dict[str, str] = {
    "૨": "ર",   # digit 2 → ra (only in word-internal positions)
    "ધ": "દ",   # safer in correction engine with dictionary verification
}


def _strip_control_chars(text: str) -> str:
    """Remove zero-width and control characters."""
    text = _ZERO_WIDTH.sub("", text)
    # Strip other control chars except newlines/tabs
    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in "\n\r\t":
            continue
        result.append(ch)
    return "".join(result)


def _nfc_normalize(text: str) -> str:
    """Apply Unicode NFC normalization."""
    return unicodedata.normalize("NFC", text)


def _fix_prebase_matras(text: str) -> str:
    """
    Fix pre-base matra ordering.
    In Unicode, the i-matra (િ U+0ABF) should follow its base consonant,
    but some legacy fonts encode it before.
    """
    return _PREBASE_MATRA_RE.sub(r"\2\1", text)


def _apply_corruption_repairs(text: str) -> str:
    """Apply known corruption repair patterns (longest-first for safety)."""
    # Sort by length descending to apply longer patterns first
    repairs = sorted(CORRUPTION_REPAIRS, key=lambda r: len(r[0]), reverse=True)
    for corrupted, correct in repairs:
        text = text.replace(corrupted, correct)
    return text


def _remove_invalid_matra_sequences(text: str) -> str:
    """
    Remove matras that appear at start of text or after whitespace
    (orphaned matras from corrupt extraction).
    """
    # Matra range U+0ABE–U+0ACD
    text = re.sub(r"(?<!\S)[\u0ABE-\u0ACD]", "", text)
    return text


def _fix_anusvara_position(text: str) -> str:
    """
    Anusvara (ં) should follow vowel signs, not precede them.
    Fix: consonant + anusvara + matra → consonant + matra + anusvara
    """
    # Pattern: base consonant + anusvara + vowel matra
    text = re.sub(
        r"([\u0A95-\u0ABA\u0ABD])\u0A82([\u0ABE-\u0ACC])",
        r"\1\2\u0A82",
        text,
    )
    return text


def _fix_chandrabindu_position(text: str) -> str:
    """Chandrabindu (ઁ) should appear after the base."""
    text = re.sub(
        r"([\u0A95-\u0ABA\u0ABD])\u0A81([\u0ABE-\u0ACC])",
        r"\1\2\u0A81",
        text,
    )
    return text


def normalize_gujarati_text(text: str) -> str:
    """
    Full Gujarati Unicode normalization pipeline.

    Stages:
      1. Strip control/zero-width characters
      2. NFC normalization
      3. Apply corruption repair maps
      4. Fix pre-base matra ordering
      5. Remove orphaned matras
      6. Fix anusvara position
      7. Fix chandrabindu position
      8. Final NFC pass

    Args:
        text: Raw text from PDF extraction or OCR.

    Returns:
        Normalized Gujarati text.
    """
    if not text:
        return text

    text = _strip_control_chars(text)
    text = _nfc_normalize(text)
    text = _apply_corruption_repairs(text)
    text = _fix_prebase_matras(text)
    text = _remove_invalid_matra_sequences(text)
    text = _fix_anusvara_position(text)
    text = _fix_chandrabindu_position(text)
    text = _nfc_normalize(text)  # Final pass after all repairs

    return text


def normalize_page_texts(texts: List[str]) -> List[str]:
    """Normalize a list of page texts."""
    return [normalize_gujarati_text(t) for t in texts]


def has_gujarati_script(text: str) -> bool:
    """Return True if text contains Gujarati Unicode characters."""
    return any("\u0A80" <= ch <= "\u0AFF" for ch in text)


def gujarati_char_ratio(text: str) -> float:
    """Return fraction of characters that are Gujarati script."""
    if not text:
        return 0.0
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    guj = sum(1 for c in chars if "\u0A80" <= c <= "\u0AFF")
    return guj / len(chars)


def detect_encoding_corruption(text: str) -> List[str]:
    """
    Detect signs of Unicode encoding corruption in Gujarati text.
    Returns a list of detected issues.
    """
    issues = []

    # Check for duplicate matras
    for matra in ["\u0ABE", "\u0ABF", "\u0AC0", "\u0AC7", "\u0ACB"]:
        if matra * 2 in text:
            issues.append(f"Duplicate matra: U+{ord(matra):04X}")

    # Check for orphaned matras (matra at start or after space)
    if re.search(r"(?:^|[\s])[\u0ABE-\u0ACD]", text):
        issues.append("Orphaned matra sequence detected")

    # Check for impossible consonant-vowel combinations
    if re.search(r"\u0A85[\u0ABE-\u0ACD]", text):
        issues.append("Standalone vowel 'a' with dependent vowel sign")

    # Check for excessive anusvara
    if re.search(r"\u0A82{2,}", text):
        issues.append("Duplicate anusvara")

    # Check for virama at end of word (incomplete halant cluster)
    if re.search(r"\u0ACD\s", text):
        issues.append("Dangling virama before space")

    return issues