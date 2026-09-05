"""Deterministic page preprocessing (OpenCV).

Design choice worth calling out: the Gemini vision extractor is given the
ORIGINAL page image, so every returned bbox lives in original-pixel space — the
same space asset crops are taken from (one coordinate system, clean provenance).

Preprocessing (grayscale / denoise / upscale / deskew) therefore serves the
deterministic OCR-accelerator path and future scanned inputs. It is applied,
recorded in provenance, and the derived image is saved for inspection — but it
does not shift the coordinate space used for extraction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class PreprocessResult:
    preprocessed_path: str
    width: int
    height: int
    ops: list[str] = field(default_factory=list)
    skew_deg: float = 0.0
    scale: float = 1.0


def preprocess_page(
    image_bytes: bytes,
    out_path: Path,
    upscale: float = 1.5,
    denoise: bool = True,
    deskew: bool = True,
) -> PreprocessResult:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("could not decode page image for preprocessing")
    ops: list[str] = ["grayscale"]

    skew = 0.0
    if deskew:
        skew = _estimate_skew(img)
        if abs(skew) > 0.2:  # only rotate if meaningfully skewed (born-digital ≈ 0)
            img = _rotate(img, skew)
            ops.append(f"deskew({skew:.2f}deg)")

    if denoise:
        img = cv2.fastNlMeansDenoising(img, h=7)
        ops.append("denoise")

    scale = 1.0
    if upscale and upscale != 1.0:
        img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        scale = float(upscale)
        ops.append(f"upscale(x{upscale})")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    h, w = img.shape[:2]
    return PreprocessResult(
        preprocessed_path=str(out_path), width=w, height=h,
        ops=ops, skew_deg=skew, scale=scale,
    )


def _estimate_skew(gray: np.ndarray) -> float:
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if coords.shape[0] < 50:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    return float(angle)


def _rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
