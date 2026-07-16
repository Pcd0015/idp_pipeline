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


def test_absent_optional_field_does_not_tank_confidence():
    """
    Regression test for the bug this was written to fix: a receipt with no
    PO number should NOT be dragged to 0% confidence just because the model
    reports low/zero confidence for a field that's correctly absent.
    """
    extraction = {
        "doc_type": "receipt",
        "vendor_name": "Big Machinery, LLC",
        "invoice_date": "2023-12-08",
        "invoice_number": "117-44332",
        "total_amount": 147.32,
        "tax_amount": 16.32,
        "po_number": None,  # correctly absent on a receipt
        "line_items": [
            {"description": "Fuel Plastic Jug (10 gallons)", "quantity": 1, "unit_price": 34, "line_total": 34},
            {"description": "Gas Can (5 feet)", "quantity": 1, "unit_price": 15, "line_total": 15},
            {"description": "Aluminum Screw (4 inches)", "quantity": 100, "unit_price": 0.82, "line_total": 82},
        ],
        "field_confidence": {
            "vendor_name": 0.93, "invoice_date": 0.9, "invoice_number": 0.88,
            "total_amount": 0.95, "tax_amount": 0.9,
            "po_number": 0.0,  # model correctly has ~no confidence about a field that isn't there
        },
    }
    result = validate_and_normalize(extraction)
    assert result["overall_confidence"] >= 0.85, (
        f"po_number's low confidence (correctly absent field) should not have "
        f"tanked the score; got {result['overall_confidence']}"
    )


def test_missing_field_confidence_dict_falls_back_gracefully():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 200.0,
        "line_items": [],
        "field_confidence": {},
    }
    result = validate_and_normalize(extraction)
    assert result["validation_errors"] == []
    assert result["overall_confidence"] == 0.5  # neutral default, not 0 and not a false "certain"


def test_confidence_on_0_to_100_scale_is_rescaled_not_clamped():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 200.0,
        "line_items": [],
        "field_confidence": {"vendor_name": 95, "invoice_date": 90, "total_amount": 97},  # 0-100 scale
    }
    result = validate_and_normalize(extraction)
    # Should be rescaled to ~0.9, not clamped to 1.0 (which would hide the formatting issue)
    assert 0.85 <= result["overall_confidence"] <= 0.95


def test_malformed_confidence_value_does_not_crash():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 200.0,
        "line_items": [],
        "field_confidence": {"vendor_name": "not-a-number", "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert result["overall_confidence"] > 0  # doesn't raise, and the two valid scores still count


def test_negative_total_amount_flagged():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": -50.0,
        "line_items": [],
        "field_confidence": {"vendor_name": 0.9, "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert any("Negative total amount" in e for e in result["validation_errors"])


def test_implausible_date_flagged():
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "0026-07-01",  # OCR-mangled year
        "total_amount": 200.0,
        "line_items": [],
        "field_confidence": {"vendor_name": 0.9, "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert any("Implausible invoice date" in e for e in result["validation_errors"])


def test_large_invoice_rounding_within_tolerance_not_flagged():
    """A few cents of float rounding on a large invoice shouldn't trigger a mismatch error."""
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 48231.47,
        "tax_amount": 0,
        "line_items": [{"description": "Bulk order", "quantity": 1, "unit_price": 48231.50, "line_total": 48231.50}],
        "field_confidence": {"vendor_name": 0.9, "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert not any("do not match declared total" in e for e in result["validation_errors"])


def test_small_invoice_real_mismatch_still_flagged():
    """Tolerance scaling shouldn't swallow a genuine mismatch on a small invoice."""
    extraction = {
        "doc_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_date": "2026-07-01",
        "total_amount": 20.0,
        "tax_amount": 0,
        "line_items": [{"description": "Widget", "quantity": 1, "unit_price": 5.0, "line_total": 5.0}],
        "field_confidence": {"vendor_name": 0.9, "invoice_date": 0.9, "total_amount": 0.9},
    }
    result = validate_and_normalize(extraction)
    assert any("do not match declared total" in e for e in result["validation_errors"])
