"""Optional deterministic OCR accelerator."""
from .ocr import OcrWord, ocr_words, tesseract_available

__all__ = ["OcrWord", "ocr_words", "tesseract_available"]
