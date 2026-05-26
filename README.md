# PDF Extraction Service

A production-grade hybrid PDF extraction API built with FastAPI, Celery, and PaddleOCR/Tesseract. Handles digital, scanned, and mixed PDFs with first-class support for Gujarati and other Indic scripts.

---

## Features

- **Hybrid extraction** — digital PDFs use PyMuPDF + pdfplumber; scanned pages go through OCR
- **Gujarati / Indic script support** — routes scanned pages to Tesseract (`guj+eng`) or PaddleOCR based on configuration
- **Async job queue** — uploads return a `job_id` immediately; Celery workers process in the background
- **Table extraction** — structured tables from both digital pages (pdfplumber) and scanned pages (grid detection + OCR tokens)
- **Deduplication** — SHA-256 hash check reuses cached results for identical uploads
- **Rate limiting** — per-endpoint SlowAPI limits configurable via `.env`
- **Frontend UI** — built-in drag-and-drop upload interface with live progress polling

---

## Architecture

```
POST /api/v1/upload
      │
      ▼
  File saved to disk → Celery task enqueued
                              │
                              ▼
                    PDF type detection (PyMuPDF)
                         ┌────┴────┐
                     Digital    Scanned
                         │          │
                   PyMuPDF +    Tesseract (Gujarati)
                  pdfplumber    or PaddleOCR (English)
                         └────┬────┘
                          Noise clean
                          Validation
                          JSON saved to disk
                              │
GET /api/v1/extract/{job_id} ◄┘
```

**Services:**
- `api` — FastAPI app, rate-limited endpoints, deduplication
- `worker` — Celery worker running the extraction pipeline
- `redis` — broker + result backend
- `flower` (optional) — Celery task monitoring UI

---

## Quick Start

### Docker (recommended)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — set SECRET_KEY and API_KEY at minimum

# 2. Build and start all services
docker compose up --build
```

The API is available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

### Local Development

**Prerequisites:** Python 3.11, Redis, Tesseract with Gujarati language pack

```bash
# Install dependencies
pip install -r requirements.txt

# Install Tesseract + Gujarati language pack (Ubuntu/Debian)
sudo apt-get install tesseract-ocr tesseract-ocr-guj

# Configure environment
cp .env.example .env
# Edit .env

# Start services (three separate terminals)
redis-server

celery -A app.workers.celery_worker worker --pool=solo --loglevel=info

uvicorn app.main:app --reload --port 8000
```

---

## Configuration

All settings are loaded from `.env`. Copy `.env.example` to get started.

### Critical settings

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Random secret for production. Startup fails if left as placeholder. |
| `API_KEY` | *(required)* | All API requests must include `X-API-Key: <value>`. Set to `""` to disable auth in dev. |
| `OCR_LANGUAGE` | `gu` | Primary OCR language. `gu` routes scanned pages to Tesseract; `en` uses PaddleOCR. |
| `OCR_DPI` | `200` | Render DPI for scanned pages. Use ≥ 150 for Gujarati; 80–96 for English-only speed. |
| `OCR_CONFIDENCE_THRESHOLD` | `0.3` | Minimum word confidence to accept. Keep ≤ 0.3 for Gujarati (model scores 0.3–0.6). |
| `CELERY_TASK_TIMEOUT` | `300` | Hard task time limit in seconds. Do not lower below 120 for scanned docs. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string. |

### OCR DPI trade-offs

| `OCR_DPI` | Speed | Accuracy | Use case |
|---|---|---|---|
| `80` | Fastest | Lower | English-only, clean scans |
| `150` | Balanced | Good | Default — Gujarati + English |
| `200` | Slower | Better | Complex Gujarati documents |
| `250` | Slowest | Best | Fine matras, small print |

---

## API Reference

All endpoints require `X-API-Key` header (unless `API_KEY` is empty).

### Upload a PDF

```
POST /api/v1/upload
Content-Type: multipart/form-data

file: <pdf file>
```

Response:
```json
{
  "job_id": "abc123",
  "filename": "document.pdf",
  "size_bytes": 204800,
  "message": "File uploaded. Use job_id to poll /extract/{job_id}."
}
```

### Poll for result

```
GET /api/v1/extract/{job_id}
```

Returns `202` while processing, `200` when complete, `410` if expired.

### Lightweight status check

```
GET /api/v1/extract/{job_id}/status
```

### Get text only

```
GET /api/v1/extract/{job_id}/text
```

### Get tables only

```
GET /api/v1/extract/{job_id}/tables
```

### Cancel and delete a job

```
DELETE /api/v1/extract/{job_id}
```

### Health probes

```
GET /ping          # Unauthenticated liveness stub (Docker/k8s probes)
GET /healthz       # Authenticated liveness
GET /readyz        # Authenticated readiness (checks Redis + Celery worker)
```

---

## Expected Performance

| Document type | Pages | Processing time |
|---|---|---|
| Digital PDF | 1–5 | 1–3 s |
| Scanned PDF (Gujarati, `OCR_DPI=150`) | 1 | 5–10 s |
| Scanned PDF (Gujarati, `OCR_DPI=150`) | 3–5 | 10–20 s |
| Mixed PDF | 10 | 20–40 s |

For faster processing of English-only scanned docs, set `OCR_DPI=80` and `OCR_LANGUAGE=en` in `.env`.

---

## Project Structure

```
app/
├── api/
│   ├── limiter.py            # Shared SlowAPI rate-limiter instance
│   ├── security.py           # API key authentication
│   └── routes/
│       ├── extract.py        # GET/DELETE /extract/{job_id}
│       ├── health.py         # /ping, /healthz, /readyz
│       └── upload.py         # POST /upload
├── config/
│   ├── constants.py          # System-wide constants
│   └── settings.py           # Pydantic settings (loaded from .env)
├── models/
│   └── response_model.py     # API request/response schemas
├── pipelines/
│   └── extraction_pipeline.py  # Master orchestrator
├── services/
│   ├── digital_extractor.py  # PyMuPDF text extraction
│   ├── gujarati_ocr.py       # Tesseract OCR for Gujarati
│   ├── ocr_extractor.py      # PaddleOCR engine + process pool
│   ├── pdf_detector.py       # Page classification (digital/scanned)
│   ├── table_extractor.py    # pdfplumber + grid-based table extraction
│   └── validator.py          # Post-processing and confidence scoring
├── utils/
│   ├── file_handler.py       # Upload streaming, dedup, cleanup
│   ├── image_preprocessing.py  # OpenCV preprocessing pipeline
│   ├── noise_cleaner.py      # Multilingual noise removal
│   └── sorting.py            # Reading-order reconstruction
├── workers/
│   └── celery_worker.py      # Celery task definition
└── main.py                   # FastAPI app factory

frontend/                     # Built-in demo UI
tests/                        # pytest test suite
docker-compose.yml
Dockerfile
requirements.txt
.env.example
```

---

## Running Tests

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

Tests cover sorting, noise cleaning, validation, API routes, PDF detection, Gujarati OCR settings, and concurrent pipeline execution. No external files or live Celery/Redis required.

---

## Flower (Task Monitoring)

To enable the Celery monitoring dashboard:

```bash
docker compose --profile monitoring up
```

Flower UI is available at `http://localhost:5555`.

---

## Optional: LayoutParser

For ML-based layout detection (multi-column documents, figure/table region classification):

```bash
pip install -r requirements-optional.txt
```

Requires Detectron2. Falls back gracefully if not installed.

---

## Notes

- Results expire after `RESULT_EXPIRES_SECONDS` (default 7200 s / 2 hours). Re-upload to reprocess.
- The Celery worker and API container must have identical OCR environment variables. The `docker-compose.yml` already ensures this.
- On Windows, the Celery worker automatically switches to `pool=solo`.
- `app/api/routes/limiter.py` is an orphaned file that can be safely deleted — the active limiter lives at `app/api/limiter.py`.