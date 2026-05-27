"""
Gujarati Intelligence Pipeline — Stages 5–9 Orchestrator
==========================================================
Integrates into the existing extraction pipeline (extraction_pipeline.py)
as a post-processing layer that runs after OCR/digital extraction.

Usage from extraction_pipeline.py:
    from app.services.gujarati_intelligence import run_intelligence_pipeline

    result = run_intelligence_pipeline(
        page_texts=cleaned_texts,
        extraction_result=extraction_result,
    )

This module adds to ExtractionResult:
  - normalized_text: Unicode-normalized, corrected text
  - parsed_document: structured field extraction
  - validation_report: field validation results
  - intelligence: dict with answers to standard queries
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.services.answer_engine import (
    DocumentAnswer,
    answers_to_dict,
    batch_answer,
    generate_answer,
)
from app.services.document_parser import (
    ParsedDocument,
    parse_multi_page_document,
)
from app.services.field_validator import (
    ValidationReport,
    apply_suggested_corrections,
    validate_parsed_document,
)
from app.services.gujarati_correction import (
    correct_pages,
    disambiguate_digit_consonant,
)
from app.utils.gujarati_unicode import (
    detect_encoding_corruption,
    gujarati_char_ratio,
    normalize_page_texts,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Standard questions automatically answered for every government document
STANDARD_QUESTIONS = [
    "What is the document type?",
    "What is the survey number?",
    "What is the owner name?",
    "What is the village?",
    "What is the taluka?",
    "What is the district?",
    "What is the date?",
    "What is the mutation number?",
    "What is the land area?",
    "What is the amount?",
]


@dataclass
class IntelligenceResult:
    """Result of the full Gujarati intelligence pipeline."""

    # Processed text
    normalized_text: str
    corrected_text: str
    normalization_issues: List[str]
    correction_changes: List[str]

    # Structured extraction
    parsed_document: ParsedDocument
    validation_report: ValidationReport

    # Standard Q&A
    standard_answers: List[Dict[str, Any]]

    # Performance
    processing_time_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for API responses."""
        return {
            "normalized_text": self.normalized_text,
            "corrected_text": self.corrected_text,
            "normalization_issues": self.normalization_issues,
            "correction_changes": self.correction_changes,
            "document_type": self.parsed_document.document_type.value,
            "structured_fields": self.parsed_document.structured_data,
            "validation": {
                "overall_valid": self.validation_report.overall_valid,
                "summary": self.validation_report.summary,
                "validated_confidence": round(self.validation_report.validated_confidence, 3),
                "field_validations": {
                    k: {
                        "is_valid": v.is_valid,
                        "issues": v.issues,
                        "suggested_value": v.suggested_value,
                    }
                    for k, v in self.validation_report.field_validations.items()
                },
                "cross_field_issues": self.validation_report.cross_field_issues,
            },
            "standard_answers": self.standard_answers,
            "processing_time_seconds": round(self.processing_time_seconds, 3),
        }


def run_intelligence_pipeline(
    page_texts: List[str],
    apply_fuzzy_correction: bool = True,
    extra_questions: Optional[List[str]] = None,
) -> IntelligenceResult:
    """
    Run the full Gujarati intelligence pipeline on extracted page texts.

    Stages:
      5. Unicode normalization
      6. Language correction (vocabulary + fuzzy)
      7. Structured document parsing
      8. Field validation
      9. Answer generation

    Args:
        page_texts: List of extracted text strings (one per page).
        apply_fuzzy_correction: Whether to run fuzzy name correction (slower).
        extra_questions: Additional user questions to answer.

    Returns:
        IntelligenceResult with all processed data.
    """
    start_time = time.time()

    # ── Stage 5: Unicode Normalization ─────────────────────────────────────
    logger.info("[Intelligence] Stage 5: Unicode normalization")

    normalized_texts = normalize_page_texts(page_texts)

    # Detect corruption in raw vs normalized
    all_normalization_issues: List[str] = []
    for i, (raw, normalized) in enumerate(zip(page_texts, normalized_texts)):
        if raw != normalized:
            issues = detect_encoding_corruption(raw)
            for issue in issues:
                all_normalization_issues.append(f"Page {i+1}: {issue}")
        guj_ratio = gujarati_char_ratio(normalized)
        if guj_ratio > 0:
            logger.debug(
                "Page %d: Gujarati char ratio=%.2f", i + 1, guj_ratio
            )

    # ── Stage 6: Language Correction ───────────────────────────────────────
    logger.info("[Intelligence] Stage 6: Language correction")

    # Apply digit/consonant disambiguation first
    disambiguated = [disambiguate_digit_consonant(t) for t in normalized_texts]

    # Apply vocabulary + fuzzy corrections
    corrected_texts, all_changes_per_page = correct_pages(
        disambiguated, apply_fuzzy=apply_fuzzy_correction
    )

    all_correction_changes: List[str] = []
    for i, changes in enumerate(all_changes_per_page):
        for change in changes:
            all_correction_changes.append(f"Page {i+1}: {change}")

    if all_correction_changes:
        logger.info(
            "[Intelligence] Applied %d corrections", len(all_correction_changes)
        )

    # Combine all page texts for multi-page analysis
    combined_corrected = "\n\n".join(corrected_texts)

    # ── Stage 7: Structured Document Parsing ───────────────────────────────
    logger.info("[Intelligence] Stage 7: Structured parsing")

    parsed_doc = parse_multi_page_document(corrected_texts)

    logger.info(
        "[Intelligence] Document type: %s | Fields: %d | Confidence: %.2f",
        parsed_doc.document_type.value,
        len(parsed_doc.fields),
        parsed_doc.confidence,
    )

    # ── Stage 8: Validation ─────────────────────────────────────────────────
    logger.info("[Intelligence] Stage 8: Field validation")

    validation = validate_parsed_document(parsed_doc)

    # Apply suggested corrections from validation
    parsed_doc = apply_suggested_corrections(parsed_doc, validation)

    logger.info(
        "[Intelligence] Validation: %s | Confidence: %.2f",
        "PASS" if validation.overall_valid else "ISSUES",
        validation.validated_confidence,
    )

    # ── Stage 9: Answer Generation ──────────────────────────────────────────
    logger.info("[Intelligence] Stage 9: Answer generation")

    questions = STANDARD_QUESTIONS + (extra_questions or [])
    answers = batch_answer(
        questions,
        doc=parsed_doc,
        corrected_text=combined_corrected,
        validation=validation,
    )

    # Set question on each answer (batch_answer preserves order)
    for ans, q in zip(answers, questions):
        ans.question = q

    answers_serialized = answers_to_dict(answers)

    processing_time = time.time() - start_time
    logger.info(
        "[Intelligence] Pipeline complete in %.2fs | stage5_issues=%d | corrections=%d",
        processing_time,
        len(all_normalization_issues),
        len(all_correction_changes),
    )

    return IntelligenceResult(
        normalized_text="\n\n".join(normalized_texts),
        corrected_text=combined_corrected,
        normalization_issues=all_normalization_issues,
        correction_changes=all_correction_changes,
        parsed_document=parsed_doc,
        validation_report=validation,
        standard_answers=answers_serialized,
        processing_time_seconds=processing_time,
    )


def answer_question(
    question: str,
    intelligence_result: IntelligenceResult,
) -> DocumentAnswer:
    """
    Answer a specific question using an already-computed intelligence result.
    This is efficient for follow-up questions — no reprocessing needed.
    """
    return generate_answer(
        question=question,
        doc=intelligence_result.parsed_document,
        corrected_text=intelligence_result.corrected_text,
        validation=intelligence_result.validation_report,
    )