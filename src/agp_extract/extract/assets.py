"""Asset preservation — crop and keep the ORIGINAL pixels for every asset block.

The original crop is provenance: even if a generated description/LaTeX/table
rendering is imperfect, nothing is silently lost — the source pixels are on disk
and hash-linked back to the page image.
"""
from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from ..schemas import AssetRef, BBox

log = logging.getLogger(__name__)


def crop_asset(
    original_bytes: bytes,
    bbox: BBox,
    out_path: Path,
    kind: str,
    source_image: str,
    pad: int = 4,
) -> Optional[AssetRef]:
    try:
        with Image.open(BytesIO(original_bytes)) as im:
            im = im.convert("RGB")
            W, H = im.size
            x0, y0, x1, y1 = bbox.as_pixel_tuple()
            x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
            x1 = min(W, x1 + pad); y1 = min(H, y1 + pad)
            if x1 - x0 < 2 or y1 - y0 < 2:
                return None
            crop = im.crop((x0, y0, x1, y1))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(out_path, format="PNG")
            data = out_path.read_bytes()
            return AssetRef(
                path=str(out_path),
                sha256=hashlib.sha256(data).hexdigest(),
                media_type="image/png",
                width=crop.width, height=crop.height,
                kind=kind, derived_from_source_image=source_image,
            )
    except Exception as exc:
        log.warning("asset crop failed (%s): %s", out_path.name, exc)
        return None
