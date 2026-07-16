"""
Outbound event notifications.

This is deliberately generic instead of a Slack-specific or vendor-specific
integration: it POSTs a JSON payload to whatever URL you put in WEBHOOK_URL.
That means it works out of the box with things that are free and require no
subscription of their own, e.g.:
  - A Slack "Incoming Webhook" URL (free, no Slack app review needed)
  - A Discord channel webhook URL (free)
  - Zapier/Make "Catch Webhook" trigger (free tier)
  - Your own server, if you want to wire this into something else

If WEBHOOK_URL is empty (the default), this is a no-op — nothing is sent
and nothing is required to keep the app running for free.
"""
import httpx

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def notify_status_change(document_id: str, filename: str, status: str, confidence: float = None):
    """Called from the (synchronous) pipeline code in app/tasks.py."""
    if not settings.webhook_url:
        return

    # Slack/Discord both render a top-level "text" field; anything else
    # (Zapier, a custom endpoint) gets the full structured payload too.
    text = f"Document '{filename}' ({document_id[:8]}...) is now {status}"
    if confidence is not None:
        text += f" -- confidence {round(confidence * 100)}%"

    payload = {
        "text": text,
        "document_id": document_id,
        "filename": filename,
        "status": status,
        "confidence_score": confidence,
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(settings.webhook_url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        # Never let a notification failure break the document pipeline.
        logger.warning("webhook_notify_failed", document_id=document_id, error=str(exc))
