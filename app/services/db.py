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
