"""
FastAPI application: the public interface to the IDP pipeline.
Upload is synchronous only up to "file saved + task enqueued" — all actual
processing happens asynchronously in Celery workers so the API stays fast
and responsive under document-volume load.

Public-deployment hardening applied here:
  - Uploads are streamed to disk in chunks and size-checked as they go
    (never buffered fully in RAM) — see app/services/storage.py
  - Per-IP rate limiting on the upload endpoint (slowapi)
  - CORS restricted to configured origins (ALLOWED_ORIGINS env var)
  - Review endpoints require an X-Admin-Key header matching ADMIN_API_KEY
  - Serves a simple drag-and-drop upload page at "/"
"""
import os
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging_config import get_logger
from app.tasks import process_document_pipeline
from app.schemas.api_schemas import DocumentStatusResponse, DocumentResultResponse, CorrectionRequest
from app.services.db import get_document_status, get_document_result, save_human_corrections, init_db
from app.services import storage

logger = get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="IDP Pipeline API",
    description="Intelligent Document Processing service — invoice/PO/receipt extraction with anomaly detection.",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_origins = ["*"] if settings.allowed_origins.strip() == "*" else [
    o.strip() for o in settings.allowed_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff"}
os.makedirs(settings.raw_storage_path, exist_ok=True)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_admin(x_admin_key: str = Header(default="")):
    """
    Dependency that gates admin/review endpoints once the app is public.
    If ADMIN_API_KEY isn't set (e.g. local dev), the endpoint stays open.
    """
    if not settings.admin_api_key:
        return
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Missing or invalid X-Admin-Key header")


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("app_startup", storage_backend=settings.storage_backend)


@app.get("/")
async def upload_page():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "IDP Pipeline API is running. See /docs."}


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/documents/upload", response_model=DocumentStatusResponse, status_code=202)
@limiter.limit(settings.rate_limit_uploads)
async def upload_document(request: Request, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {ALLOWED_EXTENSIONS}")

    document_id = str(uuid.uuid4())

    try:
        storage_key = await storage.save_upload_stream(file, document_id, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))

    logger.info("document_uploaded", document_id=document_id, filename=file.filename)

    process_document_pipeline.delay(document_id=document_id, file_path=storage_key, filename=file.filename)

    return DocumentStatusResponse(document_id=document_id, status="pending")


@app.get("/documents/{document_id}/status", response_model=DocumentStatusResponse)
async def get_status(document_id: str):
    doc = get_document_status(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@app.get("/documents/{document_id}/result", response_model=DocumentResultResponse)
async def get_result(document_id: str):
    result = get_document_result(document_id)
    if not result:
        raise HTTPException(404, "Document not found, or not yet processed")
    return result


@app.post("/documents/{document_id}/review", dependencies=[Depends(require_admin)])
async def submit_correction(document_id: str, payload: CorrectionRequest):
    """
    Human reviewer submits corrected field values. Requires X-Admin-Key
    once ADMIN_API_KEY is set. This both fixes the record and feeds the
    feedback-loop dataset used for prompt refinement / confidence
    recalibration over time.
    """
    doc = get_document_status(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    save_human_corrections(document_id, payload.corrections, reviewer=payload.reviewer)
    logger.info("human_correction_submitted", document_id=document_id, fields=list(payload.corrections.keys()))
    return {"status": "corrections_saved", "document_id": document_id}


@app.get("/documents/review-queue", dependencies=[Depends(require_admin)])
async def get_review_queue():
    """Lists documents currently awaiting human review — powers the review UI. Requires X-Admin-Key."""
    from app.services.db import get_session
    from app.models.db_models import Document

    with get_session() as db:
        docs = db.query(Document).filter(Document.status == "needs_review").all()
        return [
            {
                "document_id": str(d.id),
                "filename": d.filename,
                "confidence_score": d.confidence_score,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in docs
        ]
