"""
Celery application configuration. Import `celery_app` from here in both
the API process (to enqueue tasks) and the worker process (to run them).
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "idp_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,          # don't lose a task if a worker crashes mid-processing
    worker_prefetch_multiplier=1, # fairer distribution across workers for long-running OCR/LLM tasks
    task_time_limit=300,          # hard kill after 5 min — protects against a hung OCR/LLM call
    task_soft_time_limit=270,
)
