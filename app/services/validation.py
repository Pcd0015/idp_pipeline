"""
Post-extraction validation & normalization: schema checks, business rules,
and an aggregate confidence score used for the routing decision.
"""
from datetime import datetime

REQUIRED_FIELDS = ["vendor_name", "total_amount", "invoice_date"]

# All fields the LLM reports a per-field confidence for (see RESPONSE_SCHEMA
# in llm_extraction.py). Includes fields that are legitimately absent on some
# document types (e.g. po_number on a receipt) — see _relevant_confidences().
CONFIDENCE_FIELDS = [
    "vendor_name", "invoice_number", "po_number",
    "invoice_date", "total_amount", "tax_amount",
]

# Minimum absolute reconciliation tolerance, in currency units. Combined with
# a relative tolerance below so this scales sensibly for both a $12 receipt
# and a $50,000 invoice, where LLM/OCR rounding can differ by more than 1 cent.
MIN_ABS_TOLERANCE = 0.02
RELATIVE_TOLERANCE = 0.01  # 1% of the declared total


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _relevant_confidences(extraction: dict, field_conf: dict) -> list:
    """
    Only score confidence for fields that actually have an extracted value.

    A field the model correctly left null (e.g. po_number on a receipt, or
    tax_amount on a tax-exempt purchase) is a CORRECT "not present" — not a
    bad extraction — so it must not be included in the confidence floor.
    Missing REQUIRED fields are already penalized separately below (see
    `errors`), so excluding them here doesn't let them off the hook.

    Also defensively handles: missing per-field confidence entries, non-numeric
    values, and out-of-range values (some models occasionally return a 0-100
    scale instead of 0-1 — detected and rescaled rather than silently clamped
    to 1.0, which would mask the formatting issue as "perfect confidence").
    """
    scores = []
    for field in CONFIDENCE_FIELDS:
        value = extraction.get(field)
        if value is None or value == "":
            continue  # nothing was claimed here — nothing to score

        raw = field_conf.get(field)
        if raw is None:
            continue  # model reported a value but no confidence for it — skip rather than guess

        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue  # malformed confidence value — ignore rather than crash

        if score > 1.0:
            score = score / 100.0  # looks like a 0-100 scale — rescale instead of clamping to 1.0

        scores.append(_clamp01(score))

    return scores


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

    # 2. Date sanity check — format AND plausibility (catches obvious OCR/LLM
    #    garbage like a year of 0026 or one far in the future, not just
    #    strings that happen to parse as *a* valid calendar date).
    date_str = extraction.get("invoice_date")
    if date_str:
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
            current_year = datetime.utcnow().year
            if not (2000 <= parsed_date.year <= current_year + 1):
                errors.append(f"Implausible invoice date: {date_str}")
        except ValueError:
            errors.append(f"Invalid date format: {date_str}")

    # 3. Total amount sanity check
    total_amount = extraction.get("total_amount")
    if total_amount is not None and total_amount < 0:
        errors.append(f"Negative total amount: {total_amount}")

    # 4. Line-items + tax should reconcile with declared total.
    #    Uses an absolute+relative tolerance (not a fixed $0.01) so this
    #    doesn't false-positive on normal rounding for larger invoices,
    #    while still catching real mismatches on small ones.
    line_items = extraction.get("line_items") or []
    if line_items and total_amount is not None:
        computed_sum = sum((item.get("line_total") or 0) for item in line_items)
        tax = extraction.get("tax_amount") or 0
        difference = abs((computed_sum + tax) - total_amount)
        tolerance = max(MIN_ABS_TOLERANCE, RELATIVE_TOLERANCE * abs(total_amount))
        if difference > tolerance:
            errors.append(
                f"Line items + tax ({computed_sum + tax}) do not match declared total ({total_amount})"
            )

    # 5. Aggregate confidence: worst confidence among fields that actually
    #    have a value, penalized per validation error. Using the minimum
    #    (not average) means one badly-extracted PRESENT field can't be
    #    hidden by several easy/confident ones — but fields the model
    #    correctly left blank no longer drag the whole document to 0.
    field_conf = extraction.get("field_confidence") or {}
    scores = _relevant_confidences(extraction, field_conf)
    if scores:
        base_confidence = min(scores)
    else:
        # No usable per-field confidence signal at all (model didn't report
        # any, or every field is null). Fall back to a neutral default —
        # required-field-missing penalties below still apply if anything
        # is actually missing, so this isn't a free pass.
        base_confidence = 0.5

    penalty = 0.15 * len(errors)
    overall_confidence = _clamp01(round(base_confidence - penalty, 2))

    return {
        **extraction,
        "overall_confidence": overall_confidence,
        "validation_errors": errors,
    }
