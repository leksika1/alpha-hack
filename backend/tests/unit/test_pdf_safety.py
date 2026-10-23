import asyncio
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fpdf import FPDF
from PIL import Image

from app.integrations import ocr
from app.integrations.ocr import FakeOcr, TesseractOcr
from app.integrations.pdf import page_texts, read_pdf, render_png, validate_pdf
from app.services.analytics_ingest import IngestOutcome, ingest_directory


def make_pdf(pages: int = 1, size: tuple[int, int] = (210, 297)) -> bytes:
    pdf = FPDF(format=size)
    for _ in range(pages):
        pdf.add_page()
    return bytes(pdf.output())


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"<html>error</html>"])
def test_invalid_pdf_header_is_rejected(data: bytes) -> None:
    with pytest.raises(ValueError, match="PDF header"):
        validate_pdf(data)


def test_pdf_size_limit_and_bounded_read(tmp_path: Path) -> None:
    path = tmp_path / "large.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    with pytest.raises(ValueError, match="size limit"):
        read_pdf(path, max_bytes=1024)
    assert read_pdf(path, max_bytes=4096) == path.read_bytes()


def test_page_limit_is_checked_before_text_extraction() -> None:
    with pytest.raises(ValueError, match=r"1\.\.1 pages"):
        page_texts(make_pdf(2), max_pages=1)


@pytest.mark.parametrize("page", [0, -1, 2])
def test_render_rejects_nonexistent_page(page: int) -> None:
    with pytest.raises(ValueError, match="outside the PDF"):
        render_png(make_pdf(), page)


def test_large_page_render_obeys_pixel_limit() -> None:
    png = render_png(make_pdf(size=(1000, 1000)), 1, max_pixels=1_000_000)
    with Image.open(io.BytesIO(png)) as image:
        assert image.width * image.height <= 1_000_000
        assert image.width >= 900


async def test_concurrent_extraction_and_rendering() -> None:
    data = make_pdf()
    extracted, rendered = await asyncio.gather(
        asyncio.gather(*(asyncio.to_thread(page_texts, data) for _ in range(8))),
        asyncio.gather(*(asyncio.to_thread(render_png, data, 1, 72) for _ in range(8))),
    )
    assert all(len(pages) == 1 and pages[0].number == 1 for pages in extracted)
    assert all(png.startswith(b"\x89PNG") for png in rendered)


@pytest.mark.parametrize("failure", [TimeoutError(), asyncio.CancelledError()])
async def test_ocr_timeout_or_cancellation_kills_and_reaps_child(
    monkeypatch: Any, failure: BaseException
) -> None:
    process = Mock(returncode=None)
    process.communicate = AsyncMock(side_effect=[failure, (b"", b"")])
    monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(type(failure)):
        await TesseractOcr().recognize(b"image", 1)
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


async def test_directory_import_handles_bad_files_and_uppercase_pdf(tmp_path: Path, monkeypatch: Any) -> None:
    from app.services import analytics_ingest

    (tmp_path / "bad.pdf").write_text("not a PDF")
    (tmp_path / "good.PDF").write_bytes(make_pdf())
    (tmp_path / "ignored.txt").write_text("ignored")
    load = AsyncMock(return_value=IngestOutcome("id", "good.PDF", "ready"))
    monkeypatch.setattr(analytics_ingest, "ingest_pdf", load)
    results = await ingest_directory(None, Mock(), FakeOcr(), Mock(), tmp_path, "upload")
    assert [(r.file_name, r.status) for r in results] == [("bad.pdf", "failed"), ("good.PDF", "ready")]
    load.assert_awaited_once()


async def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="directory does not exist"):
        await ingest_directory(None, Mock(), FakeOcr(), Mock(), tmp_path / "missing", "upload")


@pytest.mark.parametrize("status", ["ready", "failed"])
async def test_cli_exits_unsuccessfully_if_any_pdf_failed(monkeypatch: Any, status: str, capsys: Any) -> None:
    from scripts import ingest

    @asynccontextmanager
    async def connection() -> Any:
        yield None

    monkeypatch.setattr(ingest, "pg_connection", connection)
    monkeypatch.setattr(
        ingest, "run_analytics_ingest", AsyncMock(return_value=[IngestOutcome("id", "doc.pdf", status)])
    )
    if status == "failed":
        with pytest.raises(SystemExit) as exc:
            await ingest._analytics(None, False, False)
        assert exc.value.code == 1
    else:
        await ingest._analytics(None, False, False)
    assert f'"{status}"' in capsys.readouterr().out
