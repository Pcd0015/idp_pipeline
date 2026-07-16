"""
Pydantic request/response models for the API layer.
"""
from typing import Optional
from pydantic import BaseModel


class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    confidence_score: Optional[float] = None


class LineItemSchema(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None


class AnomalySchema(BaseModel):
    type: str
    severity: str
    details: dict


class DocumentResultResponse(BaseModel):
    document_id: str
    status: str
    doc_type: Optional[str] = None
    confidence_score: Optional[float] = None
    fields: dict
    field_confidence: dict = {}
    validation_errors: list[str] = []
    line_items: list[LineItemSchema]
    anomalies: list[AnomalySchema]


class CorrectionRequest(BaseModel):
    corrections: dict[str, str]
    reviewer: Optional[str] = "unknown"


class DocumentListItem(BaseModel):
    document_id: str
    filename: str
    status: str
    doc_type: Optional[str] = None
    confidence_score: Optional[float] = None
    uploaded_at: Optional[str] = None
    processed_at: Optional[str] = None
    anomaly_count: int = 0
    validation_errors: list[str] = []
