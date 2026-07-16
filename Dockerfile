FROM python:3.11-slim

# System deps: tesseract for OCR, libgl for opencv, poppler for pdf handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY . .

# Run as a non-root user in production
RUN useradd -m appuser && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000
# --reload is intentionally omitted here (dev-only, docker-compose.yml
# overrides this command with --reload for local development).
# Single worker: on Render's free tier (512MB RAM), document processing
# now runs in this same process via BackgroundTasks, so we keep memory
# headroom rather than running multiple uvicorn workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
