# Gujarati Government Document Intelligence Architecture

This repository now follows a layered extraction design for real-world Gujarati
government PDFs.

## Pipeline

1. PDF classification
   - Detect digital, scanned, or mixed pages.
   - Use PyMuPDF-based text coverage checks and fallback heuristics.

2. Digital extraction
   - Prefer PyMuPDF text extraction.
   - Repair broken Gujarati fonts and malformed Unicode before cleaning.

3. Scanned-page OCR
   - Render pages at OCR-safe DPI.
   - Preprocess with OpenCV: denoise, threshold, deskew, morphology, and stamp cleanup.
   - Route to PaddleOCR or Gujarati Tesseract depending on configuration.

4. Gujarati text intelligence
   - NFC normalization and hidden control-character removal.
   - Conservative Gujarati token repair.
   - Dictionary-aware correction for common government vocabulary and locations.

5. Noise cleaning
   - Remove repeated headers, symbol-only lines, and duplicate content.
   - Apply fallback Gujarati text repair for cached extraction strings.

6. Structured extraction
   - Parse dates, survey numbers, mutation numbers, notice numbers, legal sections,
     location fields, and amounts.
   - Return extracted fields in `ExtractionResult.structured_data`.

7. Validation
   - Score extraction quality.
   - Validate structured fields and surface warnings for missing or suspicious data.

## Design Principles

- Never trust raw OCR output.
- Repair Unicode before language correction.
- Keep correction conservative to avoid damaging legitimate English or numeric text.
- Prefer page-level branching for mixed PDFs.
- Expose structured data so downstream answer generation can rely on validated fields.

## Current Limitations

- Handwriting and stamps remain difficult and should be treated as low-confidence input.
- Vocabulary-based correction is conservative and expandable.
- Full domain dictionaries for villages, talukas, and legal terms should be added
  as curated data files when available.
