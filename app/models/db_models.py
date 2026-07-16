"""
SQLAlchemy ORM models — mirrors the schema in the design doc.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, ForeignKey, DateTime, JSON, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False)
    doc_type = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    s3_raw_path = Column(String, nullable=False)
    s3_redacted_path = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    confidence_score = Column(Float, nullable=True)
    retry_count = Column(Integer, default=0)
    validation_errors = Column(JSON, nullable=True)  # list[str] — why this doc needs review

    fields = relationship("ExtractedField", back_populates="document", cascade="all, delete-orphan")
    line_items = relationship("LineItem", back_populates="document", cascade="all, delete-orphan")
    anomalies = relationship("Anomaly", back_populates="document", cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    field_name = Column(String, nullable=False)
    field_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    source = Column(String, default="llm")  # 'llm' | 'human_correction'
    bounding_box = Column(JSON, nullable=True)

    document = relationship("Document", back_populates="fields")


class LineItem(Base):
    __tablename__ = "line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    description = Column(Text, nullable=True)
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    line_total = Column(Float, nullable=True)

    document = relationship("Document", back_populates="line_items")


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    anomaly_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    resolved = Column(Boolean, default=False)

    document = relationship("Document", back_populates="anomalies")


class HumanCorrection(Base):
    __tablename__ = "human_corrections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    field_name = Column(String, nullable=False)
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)
    reviewer = Column(String, nullable=True)
    corrected_at = Column(DateTime, default=datetime.utcnow)


class PurchaseOrder(Base):
    """Reference data used by the anomaly detector to cross-check invoices."""
    __tablename__ = "purchase_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_number = Column(String, unique=True, nullable=False)
    vendor_name = Column(String, nullable=True)
    total_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
