"""
Post-extraction validation & normalization: schema checks, business rules,
and an aggregate confidence score used for the routing decision.
"""
from datetime import datetime

REQUIRED_FIELDS = ["vendor_name", "total_amount", "invoice_date"]


def validate_and_normalize(extraction: dict) -> dict:
    if extraction.get("parse_error"):
        return {
            **extraction,
            "overall_confidence": 0.0,
            "validation_errors": ["LLM output was not valid JSON"],
        }

    errors = []

    # 1. Required field presence
    for field in REQUIRED_FIELDS:
        if not extraction.get(field):
            errors.append(f"Missing required field: {field}")

    # 2. Date sanity check
    date_str = extraction.get("invoice_date")
    if date_str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            errors.append(f"Invalid date format: {date_str}")

    # 3. Line-items + tax should reconcile with declared total
    line_items = extraction.get("line_items") or []
    total_amount = extraction.get("total_amount")
    if line_items and total_amount is not None:
        computed_sum = sum((item.get("line_total") or 0) for item in line_items)
        tax = extraction.get("tax_amount") or 0
        if abs((computed_sum + tax) - total_amount) > 0.01:
            errors.append(
                f"Line items + tax ({computed_sum + tax}) do not match declared total ({total_amount})"
            )

    # 4. Aggregate confidence: worst per-field confidence, penalized per validation error.
    #    Using the minimum (not average) means one badly-extracted required field
    #    can't be hidden by several easy/confident ones.
    field_conf = extraction.get("field_confidence") or {}
    base_confidence = min(field_conf.values()) if field_conf else 0.5
    penalty = 0.15 * len(errors)
    overall_confidence = max(0.0, round(base_confidence - penalty, 2))

    return {
        **extraction,
        "overall_confidence": overall_confidence,
        "validation_errors": errors,
    }
