"""PDF reading and page rendering via pypdfium2 (Apache-2.0/BSD; chosen over AGPL PyMuPDF — ADR-023)."""

import io
import math
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import pypdfium2 as pdfium

OCR_DPI = 300
MAX_PDF_BYTES = 50_000_000
MAX_PDF_PAGES = 150
MAX_RENDER_PIXELS = 25_000_000
# PDFium cannot be called concurrently, including for different documents.
_PDFIUM_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class PdfPage:
    number: int  # 1-based
    text: str


def validate_pdf(data: bytes, max_bytes: int = MAX_PDF_BYTES) -> None:
    if len(data) > max_bytes:
        raise ValueError(f"PDF exceeds the size limit of {max_bytes} bytes")
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("File does not have a PDF header")


def read_pdf(path: Path, max_bytes: int = MAX_PDF_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    validate_pdf(data, max_bytes)
    return data


def page_texts(data: bytes, max_pages: int = MAX_PDF_PAGES, max_bytes: int = MAX_PDF_BYTES) -> list[PdfPage]:
    """Text layer of every page ('' for scanned pages)."""
    validate_pdf(data, max_bytes)
    with _PDFIUM_LOCK:
        return _page_texts(data, max_pages)


def _page_texts(data: bytes, max_pages: int) -> list[PdfPage]:
    pdf = pdfium.PdfDocument(data)
    try:
        if not 1 <= len(pdf) <= max_pages:
            raise ValueError(f"PDF must have 1..{max_pages} pages")
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            try:
                out.append(PdfPage(i + 1, textpage.get_text_range() or ""))
            finally:
                textpage.close()
                page.close()
        return out
    finally:
        pdf.close()


def render_png(
    data: bytes, page_number: int, dpi: int = OCR_DPI, max_pixels: int = MAX_RENDER_PIXELS
) -> bytes:
    if dpi <= 0 or max_pixels <= 0:
        raise ValueError("Render DPI and pixel limit must be positive")
    with _PDFIUM_LOCK:
        return _render_png(data, page_number, dpi, max_pixels)


def _render_png(data: bytes, page_number: int, dpi: int, max_pixels: int) -> bytes:
    pdf = pdfium.PdfDocument(data)
    try:
        if not 1 <= page_number <= len(pdf):
            raise ValueError("Page number is outside the PDF")
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()
            area = width * height
            if width <= 0 or height <= 0 or area <= 0 or not math.isfinite(area):
                raise ValueError("PDF page has invalid dimensions")
            scale = min(dpi / 72, math.sqrt(max_pixels / area), max_pixels / width, max_pixels / height)
            # PDFium rounds each dimension up; keep the resulting bitmap within the limit.
            while math.ceil(width * scale) * math.ceil(height * scale) > max_pixels:
                scale *= 0.99
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()
        buf = io.BytesIO()
        try:
            image.save(buf, format="PNG")
        finally:
            image.close()
        return buf.getvalue()
    finally:
        pdf.close()
