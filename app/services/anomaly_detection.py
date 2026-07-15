"""
The "value-add" layer: business-logic anomaly checks that pure extraction
accuracy alone won't catch — a document can have every field extracted
perfectly and still represent a real business problem (duplicate invoice,
PO mismatch, suspicious price deviation).
"""
from app.services.db import get_purchase_order, get_invoice_history

PRICE_DEVIATION_THRESHOLD = 0.20   # 20%
TOTAL_MISMATCH_THRESHOLD = 0.05    # 5%
TOTAL_MISMATCH_HIGH_THRESHOLD = 0.15  # 15% -> high severity


def detect(document_id: str, extracted: dict) -> list[dict]:
    anomalies = []

    if extracted.get("doc_type") != "invoice":
        return anomalies

    po_number = extracted.get("po_number")
    total_amount = extracted.get("total_amount")
    vendor = extracted.get("vendor_name")

    # 1. Referenced PO doesn't exist
    po = get_purchase_order(po_number) if po_number else None
    if po_number and not po:
        anomalies.append({
            "anomaly_type": "po_not_found",
            "severity": "high",
            "details": {"po_number": po_number},
        })

    # 2. Invoice total vs PO total mismatch
    if po and total_amount is not None and po.get("total_amount"):
        deviation_pct = abs(total_amount - po["total_amount"]) / max(po["total_amount"], 1)
        if deviation_pct > TOTAL_MISMATCH_THRESHOLD:
            anomalies.append({
                "anomaly_type": "total_mismatch",
                "severity": "high" if deviation_pct > TOTAL_MISMATCH_HIGH_THRESHOLD else "medium",
                "details": {
                    "invoice_total": total_amount,
                    "po_total": po["total_amount"],
                    "deviation_pct": round(deviation_pct * 100, 1),
                },
            })

    # 3. Duplicate invoice (same vendor + amount within a rolling window)
    if vendor and total_amount is not None:
        history = get_invoice_history(vendor=vendor, amount=total_amount, window_days=30)
        if history:
            anomalies.append({
                "anomaly_type": "duplicate_invoice",
                "severity": "high",
                "details": {"matched_document_ids": [h["id"] for h in history]},
            })

    # 4. Line-item unit price deviation vs vendor's historical average
    for item in extracted.get("line_items") or []:
        historical_avg = _get_historical_unit_price(vendor, item.get("description"))
        unit_price = item.get("unit_price")
        if historical_avg and unit_price:
            price_dev = abs(unit_price - historical_avg) / historical_avg
            if price_dev > PRICE_DEVIATION_THRESHOLD:
                anomalies.append({
                    "anomaly_type": "price_deviation",
                    "severity": "medium",
                    "details": {
                        "item": item.get("description"),
                        "invoiced_price": unit_price,
                        "historical_avg": historical_avg,
                        "deviation_pct": round(price_dev * 100, 1),
                    },
                })

    return anomalies


def _get_historical_unit_price(vendor: str, item_description: str):
    """
    Placeholder — in a full implementation, query a rolling average unit
    price for (vendor, item_description) from the line_items table.
    Left as a stub since it needs real historical data to be meaningful.
    """
    return None
