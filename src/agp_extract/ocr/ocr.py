"""Optional deterministic OCR accelerator (Tesseract via pytesseract).

This is a *seam*, not a hard dependency. When Tesseract + pytesseract are
present, ``ocr_words`` returns word-level text + confidence-aware boxes that can
anchor/verify the vision output (rapidfuzz) and feed OCR-confidence into
provenance. When absent (as in this POC environment), it degrades to ``None``
and the vision path proceeds unaffected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class OcrWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float


def tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_words(image_bytes: bytes) -> Optional[list[OcrWord]]:
    """Word boxes + confidence, or None if Tesseract is unavailable."""
    if not tesseract_available():
        return None
    import pytesseract
    from pytesseract import Output

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    data = pytesseract.image_to_data(img, output_type=Output.DICT)
    words: list[OcrWord] = []
    n = len(data["text"])
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else -1.0
        if not txt or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(OcrWord(txt, x, y, x + w, y + h, conf / 100.0))
    return words
