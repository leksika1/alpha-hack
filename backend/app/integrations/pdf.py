"""PDF reading and page rendering via pypdfium2 (Apache-2.0/BSD; chosen over AGPL PyMuPDF — ADR-023)."""

import io
from dataclasses import dataclass

import pypdfium2 as pdfium

OCR_DPI = 300


@dataclass(frozen=True, slots=True)
class PdfPage:
    number: int  # 1-based
    text: str


def page_texts(data: bytes) -> list[PdfPage]:
    """Text layer of every page ('' for scanned pages)."""
    pdf = pdfium.PdfDocument(data)
    try:
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


def render_png(data: bytes, page_number: int, dpi: int = OCR_DPI) -> bytes:
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_number - 1]
        try:
            image = page.render(scale=dpi / 72).to_pil()
        finally:
            page.close()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()
