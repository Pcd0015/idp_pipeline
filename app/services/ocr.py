"""
Adapter pattern for OCR engines: swap providers without touching the
rest of the pipeline. Defaults to Tesseract (free, local) for dev;
swap in AWS Textract / Google Document AI for production accuracy on
noisy scans, by implementing the same OCREngine interface.
"""
from abc import ABC, abstractmethod

import pytesseract
from PIL import Image

from app.core.config import settings


class OCREngine(ABC):
    @abstractmethod
    def extract(self, image_path: str) -> tuple[str, dict]:
        """Returns (full_text, layout_data)."""
        ...


class TesseractEngine(OCREngine):
    def extract(self, image_path: str) -> tuple[str, dict]:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image)
        # Word-level bounding boxes — useful later for a UI overlay that
        # highlights exactly where an extracted value came from on the page.
        layout = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        return text, layout


class TextractEngine(OCREngine):
    """
    Stub adapter for AWS Textract. Wire up boto3 when you have AWS
    credentials — the rest of the pipeline doesn't need to change since
    it only depends on the (text, layout) tuple contract.
    """
    def extract(self, image_path: str) -> tuple[str, dict]:
        raise NotImplementedError(
            "Implement boto3 textract.analyze_document() call here, "
            "map its Blocks response into the same layout dict shape."
        )


class DocumentAIEngine(OCREngine):
    """Stub adapter for Google Document AI — same idea as TextractEngine."""
    def extract(self, image_path: str) -> tuple[str, dict]:
        raise NotImplementedError("Implement google-cloud-documentai client call here.")


_ENGINES = {
    "tesseract": TesseractEngine,
    "textract": TextractEngine,
    "document_ai": DocumentAIEngine,
}


def get_engine() -> OCREngine:
    engine_cls = _ENGINES.get(settings.ocr_provider, TesseractEngine)
    return engine_cls()


def extract_text(image_path: str) -> tuple[str, dict]:
    engine = get_engine()
    return engine.extract(image_path)
