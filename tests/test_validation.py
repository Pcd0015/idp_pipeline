"""
Example unit tests for the validation layer — run with `pytest`.
These don't need Postgres/Redis/LLM running since validation.py is pure logic.
"""
from app.services.validation import validate_and_normalize


def test_missing_required_field_lowers_confidence():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": None,
        "total_amount": 500.0,
        "invoice_date": "2026-07-01",
        "line_items": [],
        "field_confidence": {"total_amount": 0.95, "invoice_date": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert "Missing required field: vendor_name" in result["validation_errors"]
    assert result["overall_confidence"] < 0.9


def test_line_item_total_mismatch_flagged():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 1000.0,
        "tax_amount": 0,
        "line_items": [{"description": "Widget", "quantity": 2, "unit_price": 100, "line_total": 200}],
        "field_confidence": {"vendor_name": 0.9, "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert any("do not match declared total" in e for e in result["validation_errors"])


def test_clean_extraction_high_confidence():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 200.0,
        "tax_amount": 0,
        "line_items": [{"description": "Widget", "quantity": 2, "unit_price": 100, "line_total": 200}],
        "field_confidence": {"vendor_name": 0.95, "invoice_date": 0.95, "total_amount": 0.97},
    }
    result = validate_and_normalize(extraction)
    assert result["validation_errors"] == []
    assert result["overall_confidence"] >= 0.9


def test_parse_error_routes_to_zero_confidence():
    extraction = {"parse_error": True, "raw_response": "not json"}
    result = validate_and_normalize(extraction)
    assert result["overall_confidence"] == 0.0
