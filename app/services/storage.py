"""
Storage abstraction: uploaded files can live on local disk (dev /
docker-compose, where the API and worker share a filesystem) or in
Backblaze B2 / any S3-compatible bucket (production, e.g. Render, where
the API and worker are separate containers with separate filesystems).

The rest of the app only ever calls save_upload_stream(), get_local_copy(),
and cleanup_local_copy() — nothing else needs to know which backend is
active. Controlled by STORAGE_BACKEND=local|b2 in settings.
"""
import os
import tempfile

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1MB — stream in chunks so a huge upload never
                          # sits fully buffered in RAM before we've even
                          # checked the size limit.


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=settings.b2_endpoint_url,
        aws_access_key_id=settings.b2_key_id,
        aws_secret_access_key=settings.b2_application_key,
    )


async def save_upload_stream(file, document_id: str, ext: str) -> str:
    """
    Streams `file` (a FastAPI UploadFile) to disk in 1MB chunks, aborting
    the instant the configured size limit is crossed.

    Returns a storage_key:
      - a local filesystem path, if STORAGE_BACKEND=local
      - "b2:<object-key>", if STORAGE_BACKEND=b2 (the file is uploaded to
        the bucket and the local temp copy is deleted immediately after)

    Raises ValueError if the file exceeds settings.max_file_size_mb.
    """
    os.makedirs(settings.raw_storage_path, exist_ok=True)
    local_path = os.path.join(settings.raw_storage_path, f"{document_id}{ext}")
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    written = 0

    try:
        with open(local_path, "wb") as f:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(
                        f"File exceeds {settings.max_file_size_mb}MB limit"
                    )
                f.write(chunk)
    except ValueError:
        if os.path.exists(local_path):
            os.remove(local_path)
        raise

    if settings.storage_backend == "b2":
        object_key = f"raw/{document_id}{ext}"
        _s3_client().upload_file(local_path, settings.b2_bucket_name, object_key)
        os.remove(local_path)
        logger.info("file_uploaded_to_b2", document_id=document_id, object_key=object_key)
        return f"b2:{object_key}"

    return local_path


def get_local_copy(storage_key: str) -> str:
    """
    Given a storage_key from save_upload_stream(), returns a local
    filesystem path the pipeline can open for OCR/preprocessing —
    downloading from B2 into a temp file first if the backend is remote.
    Always pair with cleanup_local_copy() once processing is done.
    """
    if not storage_key.startswith("b2:"):
        return storage_key  # already a local path, nothing to download

    object_key = storage_key[len("b2:"):]
    ext = os.path.splitext(object_key)[1]
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(tmp_fd)
    _s3_client().download_file(settings.b2_bucket_name, object_key, tmp_path)
    return tmp_path


def cleanup_local_copy(storage_key: str, local_path: str) -> None:
    """Removes the temp file created by get_local_copy(). No-op for local backend."""
    if storage_key.startswith("b2:") and local_path and os.path.exists(local_path):
        os.remove(local_path)
