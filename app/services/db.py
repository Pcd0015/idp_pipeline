"""
Database session management + query helpers used by the API layer and
Celery tasks. Kept as plain functions (rather than a repository class)
to keep the pipeline code in tasks.py easy to read.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.db_models import (
    Base, Document, ExtractedField, LineItem, Anomaly, HumanCorrection, PurchaseOrder
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create tables. In production, use Alembic migrations instead."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------- Document lifecycle ----------

def create_document_record(document_id: str, filename: str, raw_path: str):
    with get_session() as db:
        doc = Document(id=document_id, filename=filename, s3_raw_path=raw_path, status="pending")
        db.add(doc)


def update_document_status(document_id: str, status: str, confidence: float = None):
    with get_session() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return
        doc.status = status
        if confidence is not None:
            doc.confidence_score = confidence
        if status in ("completed", "needs_review", "failed"):
            doc.processed_at = datetime.utcnow()


def get_document_status(document_id: str) -> dict | None:
    with get_session() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return None
        return {
            "document_id": str(doc.id),
            "status": doc.status,
            "confidence_score": doc.confidence_score,
        }


def get_document_result(document_id: str) -> dict | None:
    with get_session() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc or doc.status not in ("completed", "needs_review"):
            return None

        fields = {f.field_name: f.field_value for f in doc.fields}
        field_confidence = {f.field_name: f.confidence for f in doc.fields if f.confidence is not None}
        line_items = [
            {"description": li.description, "quantity": li.quantity,
             "unit_price": li.unit_price, "line_total": li.line_total}
            for li in doc.line_items
        ]
        anomalies = [
            {"type": a.anomaly_type, "severity": a.severity, "details": a.details}
            for a in doc.anomalies
        ]
        return {
            "document_id": str(doc.id),
            "status": doc.status,
            "doc_type": doc.doc_type,
            "confidence_score": doc.confidence_score,
            "fields": fields,
            "field_confidence": field_confidence,
            "validation_errors": doc.validation_errors or [],
            "line_items": line_items,
            "anomalies": anomalies,
        }


# ---------- Persist extraction output ----------

def save_extracted_fields(document_id: str, validated_result: dict):
    with get_session() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return
        doc.doc_type = validated_result.get("doc_type")
        doc.validation_errors = validated_result.get("validation_errors") or []

        field_conf = validated_result.get("field_confidence", {})
        skip_keys = {"line_items", "field_confidence", "overall_confidence",
                     "validation_errors", "parse_error", "raw_response"}

        for key, value in validated_result.items():
            if key in skip_keys or value is None:
                continue
            db.add(ExtractedField(
                document_id=document_id,
                field_name=key,
                field_value=str(value),
                confidence=field_conf.get(key),
                source="llm",
            ))

        for item in validated_result.get("line_items", []):
            db.add(LineItem(
                document_id=document_id,
                description=item.get("description"),
                quantity=item.get("quantity"),
                unit_price=item.get("unit_price"),
                line_total=item.get("line_total"),
            ))


def save_anomalies(document_id: str, anomalies: list[dict]):
    with get_session() as db:
        for a in anomalies:
            db.add(Anomaly(
                document_id=document_id,
                anomaly_type=a["anomaly_type"],
                severity=a["severity"],
                details=a.get("details", {}),
            ))


def save_human_corrections(document_id: str, corrections: dict, reviewer: str = "unknown"):
    """corrections = {"field_name": "corrected_value", ...}"""
    with get_session() as db:
        for field_name, corrected_value in corrections.items():
            existing = (
                db.query(ExtractedField)
                .filter_by(document_id=document_id, field_name=field_name)
                .first()
            )
            original_value = existing.field_value if existing else None

            db.add(HumanCorrection(
                document_id=document_id,
                field_name=field_name,
                original_value=original_value,
                corrected_value=corrected_value,
                reviewer=reviewer,
            ))

            if existing:
                existing.field_value = corrected_value
                existing.source = "human_correction"
                existing.confidence = 1.0
            else:
                db.add(ExtractedField(
                    document_id=document_id,
                    field_name=field_name,
                    field_value=corrected_value,
                    confidence=1.0,
                    source="human_correction",
                ))

        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "completed"
            doc.processed_at = datetime.utcnow()


def list_documents(status: str = None, limit: int = 500) -> list[dict]:
    """Powers the dashboard, the review queue, and CSV/JSON export. Newest first."""
    with get_session() as db:
        q = db.query(Document)
        if status:
            q = q.filter(Document.status == status)
        docs = q.order_by(Document.uploaded_at.desc()).limit(limit).all()
        return [
            {
                "document_id": str(d.id),
                "filename": d.filename,
                "status": d.status,
                "doc_type": d.doc_type,
                "confidence_score": d.confidence_score,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "processed_at": d.processed_at.isoformat() if d.processed_at else None,
                "anomaly_count": len(d.anomalies),
                "validation_errors": d.validation_errors or [],
            }
            for d in docs
        ]


def list_documents_for_export(status: str = None) -> list[dict]:
    """One flattened row per document (fields collapsed into columns) for CSV/JSON export."""
    with get_session() as db:
        q = db.query(Document)
        if status:
            q = q.filter(Document.status == status)
        docs = q.order_by(Document.uploaded_at.desc()).all()
        rows = []
        for d in docs:
            row = {
                "document_id": str(d.id),
                "filename": d.filename,
                "status": d.status,
                "doc_type": d.doc_type,
                "confidence_score": d.confidence_score,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "processed_at": d.processed_at.isoformat() if d.processed_at else None,
                "anomaly_types": ";".join(a.anomaly_type for a in d.anomalies),
            }
            for f in d.fields:
                row[f.field_name] = f.field_value
            rows.append(row)
        return rows


# ---------- Reference data lookups (used by anomaly detection) ----------

def get_purchase_order(po_number: str) -> dict | None:
    if not po_number:
        return None
    with get_session() as db:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()
        if not po:
            return None
        return {"po_number": po.po_number, "vendor_name": po.vendor_name, "total_amount": po.total_amount}


def get_invoice_history(vendor: str, amount: float, window_days: int = 30) -> list[dict]:
    if not vendor or amount is None:
        return []
    cutoff = datetime.utcnow() - timedelta(days=window_days)
    with get_session() as db:
        matches = (
            db.query(Document)
            .join(ExtractedField)
            .filter(
                ExtractedField.field_name == "vendor_name",
                ExtractedField.field_value == vendor,
                Document.uploaded_at >= cutoff,
            )
            .all()
        )
        results = []
        for doc in matches:
            total_field = next((f for f in doc.fields if f.field_name == "total_amount"), None)
            if total_field and total_field.field_value:
                try:
                    if abs(float(total_field.field_value) - amount) < 0.01:
                        results.append({"id": str(doc.id)})
                except ValueError:
                    continue
        return results
