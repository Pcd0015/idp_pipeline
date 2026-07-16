"""
Centralized configuration, loaded from environment variables (.env in dev).
Never hardcode secrets — everything sensitive comes from the environment.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://idp_user:idp_password@localhost:5432/idp_db"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # LLM
    gemini_api_key: str = ""
    # gemini-2.5-flash is a stable, cheap, well-tested default for extraction tasks.
    # For higher accuracy on messy/complex layouts, gemini-3.1-pro is available;
    # for the newest fast tier, gemini-3.5-flash. Confirm current model names/pricing
    # at https://ai.google.dev/gemini-api/docs/models before switching in production.
    # gemini-2.5-flash was deprecated for new Gemini API keys (mid-2026) —
    # gemini-3.1-flash-lite is the current cost-efficient equivalent.
    llm_model: str = "gemini-3.1-flash-lite"

    # OCR
    ocr_provider: str = "tesseract"

    # Storage
    storage_backend: str = "local"  # local | b2
    raw_storage_path: str = "storage/raw"
    redacted_storage_path: str = "storage/redacted"

    # Backblaze B2 (S3-compatible) — only needed when storage_backend=b2
    b2_endpoint_url: str = ""
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket_name: str = ""

    # App behavior
    confidence_threshold: float = 0.80
    max_file_size_mb: int = 20
    log_level: str = "INFO"

    # Public deployment safety
    admin_api_key: str = ""          # required to hit review-queue / review endpoints
    allowed_origins: str = "*"       # comma-separated list, or "*" for dev
    rate_limit_uploads: str = "5/hour"

    # Processing mode: True = enqueue to a separate Celery worker (docker-compose,
    # or a paid Render worker). False = process in-process via FastAPI
    # BackgroundTasks, right after upload — used for Render's free tier,
    # which doesn't include a free background-worker service type.
    use_celery: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
