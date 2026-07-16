# IDP Pipeline — Intelligent Document Processing

An end-to-end invoice/PO/receipt processing service: OCR → PII redaction →
LLM structured extraction (Google Gemini) → validation → anomaly detection →
confidence-based routing to human review, with a feedback loop that
captures corrections for future prompt refinement.

See `IDP_System_Design.md` (if included alongside this repo) for the full
architecture writeup, diagram, and rationale.

## Quick Start (Docker — recommended)

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY (get one at https://aistudio.google.com/apikey)

docker-compose up --build
```

This starts:
- `api` — FastAPI app on http://localhost:8000 (docs at `/docs`)
- `worker` — Celery worker processing the pipeline
- `postgres` — metadata/results store
- `redis` — Celery broker/result backend
- `flower` — Celery monitoring UI on http://localhost:5555

## Quick Start (local, no Docker)

Requires: Python 3.11+, PostgreSQL running locally, Redis running locally,
and `tesseract-ocr` installed on your system (`brew install tesseract` /
`apt-get install tesseract-ocr`).

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
python -m spacy download en_core_web_sm

cp .env.example .env
# edit .env: set DATABASE_URL, REDIS_URL, GEMINI_API_KEY to your local values

# terminal 1 — API
uvicorn app.main:app --reload

# terminal 2 — worker
celery -A app.celery_app.celery_app worker --loglevel=info
```

## Usage

```bash
# Upload a document
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@sample_invoice.pdf"
# -> {"document_id": "...", "status": "pending"}

# Poll status
curl http://localhost:8000/documents/{document_id}/status

# Get extracted result once completed/needs_review
curl http://localhost:8000/documents/{document_id}/result

# See what's waiting for human review
curl http://localhost:8000/documents/review-queue

# Submit a human correction (also marks the doc completed)
curl -X POST http://localhost:8000/documents/{document_id}/review \
  -H "Content-Type: application/json" \
  -d '{"corrections": {"vendor_name": "Correct Vendor Pvt Ltd"}, "reviewer": "you"}'
```

## Running tests

```bash
pytest tests/ -v
```

(`test_validation.py` covers the validation logic in isolation — no DB/Redis/LLM required.)

## Project Layout

```
app/
├── main.py                 # FastAPI routes
├── celery_app.py           # Celery config
├── tasks.py                # Pipeline orchestration (the 7-stage flow)
├── services/
│   ├── preprocessing.py    # deskew/denoise
│   ├── ocr.py               # OCR adapter (Tesseract / Textract / Document AI)
│   ├── pii_redaction.py     # Presidio-based PII redaction
│   ├── llm_extraction.py    # Gemini-based structured extraction
│   ├── validation.py        # schema + business-rule validation, confidence scoring
│   ├── anomaly_detection.py # PO mismatch / duplicate / price-deviation checks
│   └── db.py                 # DB session + query helpers
├── models/db_models.py     # SQLAlchemy ORM models
├── schemas/api_schemas.py  # Pydantic request/response models
└── core/                   # config + logging
tests/
```

## Sample Data

For a quick demo without real client documents, the **SROIE dataset**
(scanned receipts with labeled fields) is a good public source of sample
invoices/receipts to upload and test against.

## Known Gaps / Next Steps (be upfront about these in a demo)

- No auth on the API yet — add before any real deployment.
- `TextractEngine` / `DocumentAIEngine` in `ocr.py` are stubs — only
  Tesseract is wired up end-to-end.
- `_get_historical_unit_price` in `anomaly_detection.py` is a stub —
  needs a rolling-average query once there's real historical line-item data.
- Multi-page PDF rasterization isn't implemented in `preprocessing.py` —
  add `pdf2image` there for multi-page support.
- No frontend/review UI included — `/documents/review-queue` and
  `/documents/{id}/review` are ready to be wired to one.
