"""Document ingestion — a folder of page images, a PDF, or a ZIP → ordered pages.

Every page's bytes are SHA-256 hashed at load time so the entire downstream
output is verifiable against the exact input (document-level provenance).

If a real PDF *with a text layer* is supplied, PyMuPDF could harvest text+spans
deterministically (cheap, exact) — a hook noted here for later. This sample is
image-only, so we render/serve page images and let the vision path read them.

ZIP archives are extracted to a temp directory (with path-traversal, size, and
file-count guards — see :func:`_safe_extract_zip`) and then recursively walked
for supported files, which are dispatched through the same image/PDF loaders
below. Unsupported files inside a ZIP are skipped, not errors.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..schemas import SourceFileProvenance

log = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
          ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp",
          ".webp": "image/webp"}
_TEXT_EXTS = {".txt"}
_SUPPORTED_EXTS = _IMAGE_EXTS | _TEXT_EXTS | {".pdf"}

# ZIP safety limits — generous enough for a multi-hundred-page scanned chapter,
# tight enough to stop a zip bomb or an accidental multi-GB upload.
_ZIP_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB total, extracted
_ZIP_MAX_FILE_COUNT = 2000
_ZIP_MAX_COMPRESSION_RATIO = 100  # uncompressed/compressed per member


class UnsupportedInputError(ValueError):
    """Raised for a malformed/empty/unsafe input the caller should show to a user."""


@dataclass
class LoadedPage:
    page_index: int
    image_bytes: bytes
    media_type: str
    width: int
    height: int
    source: SourceFileProvenance


@dataclass
class IngestReport:
    """Summary of what an input resolved to — surfaced by the UI, not used internally."""
    input_type: str  # "zip" | "pdf" | "image" | "txt" | "folder"
    files_discovered: int = 0
    files_supported: int = 0
    ignored: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)
    page_count: int = 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dims(data: bytes) -> tuple[int, int]:
    from io import BytesIO
    with Image.open(BytesIO(data)) as im:
        return im.width, im.height


def load_document(input_path: str | Path, max_pages: int = 0) -> list[LoadedPage]:
    pages, _report = load_document_with_report(input_path, max_pages=max_pages)
    return pages


def load_document_with_report(
    input_path: str | Path, max_pages: int = 0,
) -> tuple[list[LoadedPage], IngestReport]:
    """Like :func:`load_document`, but also returns an :class:`IngestReport`
    (discovered/supported/ignored file counts) for display in a UI.

    A ZIP is extracted to a temp directory that is always cleaned up, even on
    error. A single ``.txt`` file is passed through as one text-only "page"
    (no image, so the vision extraction step is skipped for it downstream —
    the extractor treats a missing ``image_bytes``-derived block source as a
    plain-text block instead).
    """
    p = Path(input_path)
    if not p.exists():
        raise FileNotFoundError(f"input path not found: {p}")

    if p.is_file() and p.suffix.lower() == ".zip":
        tmp_dir = Path(tempfile.mkdtemp(prefix="agp_zip_"))
        try:
            report = _safe_extract_zip(p, tmp_dir)
            pages = _load_mixed_folder(tmp_dir, report)
            report.page_count = len(pages)
            if max_pages and max_pages > 0:
                pages = pages[:max_pages]
            log.info("ingested %d page(s) from ZIP %s (%d/%d files supported)",
                      len(pages), p.name, report.files_supported, report.files_discovered)
            return pages, report
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if p.is_dir():
        report = IngestReport(input_type="folder")
        pages = _load_mixed_folder(p, report)
    elif p.suffix.lower() == ".pdf":
        report = IngestReport(input_type="pdf", files_discovered=1, files_supported=1)
        pages = _load_pdf(p)
    elif p.suffix.lower() in _IMAGE_EXTS:
        report = IngestReport(input_type="image", files_discovered=1, files_supported=1)
        pages = _load_image_folder(p.parent, only=[p])
    elif p.suffix.lower() in _TEXT_EXTS:
        report = IngestReport(input_type="txt", files_discovered=1, files_supported=1)
        pages = _load_text_files([p])
    else:
        raise UnsupportedInputError(f"unsupported input: {p.name}")

    if max_pages and max_pages > 0:
        pages = pages[:max_pages]
    report.page_count = len(pages)
    log.info("ingested %d page(s) from %s", len(pages), p)
    return pages, report


def _load_mixed_folder(folder: Path, report: IngestReport) -> list[LoadedPage]:
    """Recursively walk ``folder`` (a plain directory or an extracted ZIP),
    collecting images/PDFs/txt in deterministic order and recording what was
    ignored. A folder containing a single PDF is treated as "the document is
    that PDF"; otherwise every image (and, if no images exist, every txt) is
    a page.
    """
    all_files = sorted(f for f in folder.rglob("*") if f.is_file())
    report.files_discovered = len(all_files)

    pdfs = [f for f in all_files if f.suffix.lower() == ".pdf"]
    images = [f for f in all_files if f.suffix.lower() in _IMAGE_EXTS]
    texts = [f for f in all_files if f.suffix.lower() in _TEXT_EXTS]
    other = [f for f in all_files if f.suffix.lower() not in _SUPPORTED_EXTS]

    for f in other:
        report.ignored.append((str(f.relative_to(folder)), "unsupported file type"))

    if images:
        for f in pdfs + texts:
            report.ignored.append(
                (str(f.relative_to(folder)), "ignored — image pages take precedence"))
        report.files_supported = len(images)
        return _load_image_folder(folder, only=images)

    if pdfs:
        if len(pdfs) > 1:
            for f in pdfs[1:]:
                report.ignored.append((str(f.relative_to(folder)), "ignored — only one PDF supported per input"))
        for f in texts:
            report.ignored.append((str(f.relative_to(folder)), "ignored — a PDF takes precedence"))
        report.files_supported = 1
        return _load_pdf(pdfs[0])

    if texts:
        report.files_supported = len(texts)
        return _load_text_files(texts)

    raise UnsupportedInputError(
        "no supported files found (expected PDF, image, or .txt)")


def _safe_extract_zip(zip_path: Path, dest: Path) -> IngestReport:
    report = IngestReport(input_type="zip")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise UnsupportedInputError(f"malformed ZIP archive: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise UnsupportedInputError("ZIP archive is empty")
        if len(infos) > _ZIP_MAX_FILE_COUNT:
            raise UnsupportedInputError(
                f"ZIP contains {len(infos)} files (limit {_ZIP_MAX_FILE_COUNT})")

        total_uncompressed = 0
        for info in infos:
            # path traversal guard: reject absolute paths, drive letters, and
            # any ".." component before it ever touches the filesystem.
            name = info.filename.replace("\\", "/")
            parts = [seg for seg in name.split("/") if seg not in ("", ".")]
            if ".." in parts or Path(name).is_absolute() or (len(name) > 1 and name[1] == ":"):
                raise UnsupportedInputError(f"unsafe path in ZIP: {info.filename}")

            total_uncompressed += info.file_size
            if total_uncompressed > _ZIP_MAX_UNCOMPRESSED_BYTES:
                raise UnsupportedInputError(
                    f"ZIP exceeds the {_ZIP_MAX_UNCOMPRESSED_BYTES // (1024*1024)} MB extracted-size limit")
            if info.compress_size > 0:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > _ZIP_MAX_COMPRESSION_RATIO:
                    raise UnsupportedInputError(
                        f"suspicious compression ratio in ZIP member: {info.filename}")

            suffix = Path(name).suffix.lower()
            if suffix in (".exe", ".dll", ".sh", ".bat", ".cmd", ".ps1", ".msi", ".com", ".scr"):
                report.ignored.append((name, "executable content not allowed"))
                continue

            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

    return report


def _load_text_files(files: list[Path]) -> list[LoadedPage]:
    """A ``.txt`` file becomes a page: its text is rasterized onto a plain
    white page image, so it flows through the existing vision-extraction path
    unchanged rather than requiring a second, text-only code path through the
    5-phase pipeline.
    """
    import textwrap
    from io import BytesIO

    from PIL import ImageDraw, ImageFont

    out: list[LoadedPage] = []
    for idx, f in enumerate(files):
        text = f.read_text(encoding="utf-8", errors="replace")
        width, height, margin = 1200, 1688, 60
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        wrapped = "\n".join(
            "\n".join(textwrap.wrap(line, width=95) or [""])
            for line in text.splitlines()
        )
        draw.multiline_text((margin, margin), wrapped, fill="black", font=font, spacing=6)
        buf = BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        out.append(LoadedPage(
            page_index=idx, image_bytes=data, media_type="image/png",
            width=width, height=height,
            source=SourceFileProvenance(
                path=str(f), filename=f.name, media_type="image/png",
                sha256=_sha256(data), bytes=len(data), width=width, height=height,
                page_index=idx,
            ),
        ))
    return out


def _load_image_folder(folder: Path, only: list[Path] | None = None) -> list[LoadedPage]:
    files = only or sorted(
        f for f in folder.iterdir() if f.suffix.lower() in _IMAGE_EXTS
    )
    out: list[LoadedPage] = []
    for idx, f in enumerate(files):
        data = f.read_bytes()
        w, h = _dims(data)
        media = _MEDIA.get(f.suffix.lower(), "image/jpeg")
        out.append(LoadedPage(
            page_index=idx, image_bytes=data, media_type=media, width=w, height=h,
            source=SourceFileProvenance(
                path=str(f), filename=f.name, media_type=media,
                sha256=_sha256(data), bytes=len(data), width=w, height=h,
                page_index=idx,
            ),
        ))
    return out


def _load_pdf(pdf: Path, dpi: int = 200) -> list[LoadedPage]:
    """Render PDF pages to PNG images. (Text-layer harvest is a future hook.)"""
    import fitz  # PyMuPDF

    out: list[LoadedPage] = []
    doc = fitz.open(pdf)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for idx, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        data = pix.tobytes("png")
        w, h = pix.width, pix.height
        out.append(LoadedPage(
            page_index=idx, image_bytes=data, media_type="image/png",
            width=w, height=h,
            source=SourceFileProvenance(
                path=f"{pdf}#page={idx + 1}", filename=f"{pdf.stem}_p{idx + 1}.png",
                media_type="image/png", sha256=_sha256(data), bytes=len(data),
                width=w, height=h, page_index=idx,
                printed_page=idx + 1,
            ),
        ))
    doc.close()
    return out
