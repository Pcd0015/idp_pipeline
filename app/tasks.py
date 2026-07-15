"""
Celery task(s) that orchestrate the full IDP pipeline. Kept as one task
with clear stages (rather than a chain of many small tasks) so the whole
run is easy to trace in logs and easy to retry as a unit.
"""
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services import preprocessing, ocr, pii_redaction, llm_extraction, validation, anomaly_detection, storage
from app.services.db import (
    create_document_record, update_document_status, save_extracted_fields, save_anomalies
)

logger = get_logger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, name="process_document_pipeline")
def process_document_pipeline(self, document_id: str, file_path: str, filename: str = ""):
    """
    `file_path` here is actually a storage_key (see app/services/storage.py)
    — a local path when STORAGE_BACKEND=local, or "b2:<object-key>" when
    STORAGE_BACKEND=b2. The API and worker are separate processes/containers
    in production, so the worker downloads its own local working copy
    before running OCR, and cleans it up when done.
    """
    log = logger.bind(document_id=document_id)
    local_path = None
    try:
        create_document_record(document_id, filename or file_path, file_path)
        update_document_status(document_id, "processing")
        log.info("pipeline_started")

        local_path = storage.get_local_copy(file_path)

        # Stage 1: Pre-processing (deskew, denoise, binarize)
        clean_image_path = preprocessing.deskew_and_denoise(local_path)
        log.info("preprocessing_done")

        # Stage 2: OCR
        raw_text, ocr_layout = ocr.extract_text(clean_image_path)
        log.info("ocr_done", text_length=len(raw_text))

        if not raw_text.strip():
            update_document_status(document_id, "failed")
            log.error("ocr_produced_no_text")
            return

        # Stage 3: PII redaction — happens BEFORE the text touches the LLM API or logs
        redacted_text, pii_map = pii_redaction.redact(raw_text)
        log.info("pii_redaction_done", entities_found=len(pii_map))

        # Stage 4: LLM structured extraction
        extraction_result = llm_extraction.extract_structured_fields(
            redacted_text, layout_hints=ocr_layout
        )
        log.info("llm_extraction_done", doc_type=extraction_result.get("doc_type"))

        # Stage 5: Validation & normalization
        validated_result = validation.validate_and_normalize(extraction_result)
        log.info("validation_done",
                  overall_confidence=validated_result.get("overall_confidence"),
                  error_count=len(validated_result.get("validation_errors", [])))

        # Stage 6: Anomaly detection (business-rule checks)
        anomalies = anomaly_detection.detect(document_id, validated_result)
        if anomalies:
            log.info("anomalies_detected", count=len(anomalies),
                      types=[a["anomaly_type"] for a in anomalies])

        # Persist results
        save_extracted_fields(document_id, validated_result)
        if anomalies:
            save_anomalies(document_id, anomalies)

        # Stage 7: Confidence-based routing
        overall_confidence = validated_result.get("overall_confidence", 0.0)
        if overall_confidence < settings.confidence_threshold or anomalies:
            update_document_status(document_id, "needs_review", confidence=overall_confidence)
            log.info("routed_to_human_review", confidence=overall_confidence)
        else:
            update_document_status(document_id, "completed", confidence=overall_confidence)
            log.info("pipeline_completed", confidence=overall_confidence)

    except Exception as exc:
        log.error("pipeline_failed", error=str(exc), exc_info=True)
        update_document_status(document_id, "failed")
        raise self.retry(exc=exc)
    finally:
        if local_path:
            storage.cleanup_local_copy(file_path, local_path)
