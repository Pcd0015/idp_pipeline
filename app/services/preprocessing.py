"""
Image pre-processing: deskew + denoise before OCR.
Scanned documents are rarely perfectly aligned/clean — skipping this step
is one of the most common reasons OCR accuracy tanks in production.
"""
import os
import cv2
import numpy as np


def deskew_and_denoise(file_path: str) -> str:
    """
    Returns path to a cleaned-up image ready for OCR.
    PDFs are passed through as-is here — in production, add a pdf2image
    conversion step before this function if the input is a multi-page PDF.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        # For a full implementation: use pdf2image.convert_from_path to
        # rasterize each page, then run this same cleanup per page.
        return file_path

    image = cv2.imread(file_path)
    if image is None:
        return file_path

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Denoise
    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    # Binarize (Otsu's thresholding) — sharpens text edges for OCR
    _, binarized = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Deskew based on minAreaRect of text pixels
    coords = np.column_stack(np.where(binarized > 0))
    angle = 0.0
    if len(coords) > 0:
        rect_angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + rect_angle) if rect_angle < -45 else -rect_angle

    (h, w) = binarized.shape[:2]
    center = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    deskewed = cv2.warpAffine(
        binarized, rotation_matrix, (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )

    output_path = file_path.replace(ext, f"_clean{ext}")
    cv2.imwrite(output_path, deskewed)
    return output_path
