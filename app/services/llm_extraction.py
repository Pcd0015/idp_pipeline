"""
Converts raw (redacted) OCR text into structured, schema-validated JSON
using Google Gemini. This is the layer that handles layout variation
across vendors/formats that a fixed regex/template extractor can't.

Uses Gemini's native structured output (response_mime_type +
response_schema) instead of prompt-only JSON instructions — this makes
the model's output conform to the schema at generation time, which is
more reliable than hoping the model follows JSON formatting instructions.
"""
import json

import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

_model = None

EXTRACTION_PROMPT = """You are a document data extraction engine. Extract structured fields
from the invoice/receipt/purchase-order text below.

Rules:
- If a field is not present in the text, use null — do not guess or hallucinate values.
- field_confidence must reflect your certainty for EACH field individually, based on how
  clearly it appeared in the text, not one global number applied to everything.
- Dates must be normalized to YYYY-MM-DD.
- Amounts must be plain numbers (no currency symbols, no thousands separators).
- Some personal names/emails/phone numbers in this text have been replaced with placeholder
  tokens like <PERSON> during redaction — treat those as redacted, not missing, and do not
  try to reconstruct them.

Document text:
---
{document_text}
---
"""

# Gemini's structured-output schema (JSON Schema subset it supports).
# This constrains generation directly, rather than relying on prompt instructions alone.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["invoice", "purchase_order", "receipt"]},
        "vendor_name": {"type": "string", "nullable": True},
        "invoice_number": {"type": "string", "nullable": True},
        "po_number": {"type": "string", "nullable": True},
        "invoice_date": {"type": "string", "nullable": True},
        "total_amount": {"type": "number", "nullable": True},
        "tax_amount": {"type": "number", "nullable": True},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "line_total": {"type": "number"},
                },
            },
        },
        "field_confidence": {
            "type": "object",
            "properties": {
                "vendor_name": {"type": "number"},
                "invoice_number": {"type": "number"},
                "po_number": {"type": "number"},
                "invoice_date": {"type": "number"},
                "total_amount": {"type": "number"},
                "tax_amount": {"type": "number"},
            },
        },
    },
    "required": ["doc_type", "field_confidence"],
}


def _get_model():
    global _model
    if _model is None:
        genai.configure(api_key=settings.gemini_api_key)
        _model = genai.GenerativeModel(
            model_name=settings.llm_model,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": RESPONSE_SCHEMA,
                "temperature": 0.1,  # low temperature — this is an extraction task, not creative writing
                "max_output_tokens": 1500,
            },
        )
    return _model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_llm(prompt: str) -> str:
    model = _get_model()
    response = model.generate_content(prompt)
    return response.text.strip()


def extract_structured_fields(document_text: str, layout_hints: dict = None) -> dict:
    prompt = EXTRACTION_PROMPT.format(document_text=document_text)
    raw = _call_llm(prompt)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fail loudly into the pipeline rather than silently passing bad data
        # downstream — validation.py will route this to human review.
        parsed = {"parse_error": True, "raw_response": raw, "field_confidence": {}}

    return parsed
