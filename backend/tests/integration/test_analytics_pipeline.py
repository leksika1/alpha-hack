"""Bank analytics on Postgres+pgvector and MinIO: PDF -> S3 -> pages (text layer / OCR) -> chunks ->
embeddings + tsvector -> hybrid search with page citations -> presigned link to the page."""

import asyncio
import io
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pypdfium2 as pdfium
import pytest
from fpdf import FPDF

from app.core.config import get_settings
from app.core.pg import pg_connection
from app.core.s3 import S3ObjectStore, get_s3_client
from app.domain.ports import OcrPage
from app.integrations.embeddings import EmbeddingUnavailableError, FakeEmbedding
from app.integrations.ocr import FakeOcr, TesseractOcr
from app.services.analytics_docs import download_link, list_documents
from app.services.analytics_ingest import ingest_directory, ingest_pdf, reembed
from app.services.analytics_search import hybrid_search
from tests.unit.test_contracts import _validate_component

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

ROOT = Path(__file__).resolve().parents[2]
SYNTH = ROOT / "data" / "synthetic" / "analytics"
FONT = ROOT / "data" / "reference" / "fonts" / "Roboto-Regular.ttf"
TESSERACT = shutil.which("tesseract") or next(
    (p for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",) if Path(p).is_file()), None
)


class RecordingOcr:
    """Stands in for Tesseract to exercise the OCR path (render, cache, method) without the binary."""

    name = "ocr_tesseract"

    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, image_png: bytes, page: int) -> OcrPage:
        self.calls += 1
        assert image_png.startswith(b"\x89PNG")
        text = (
            "Сканированный обзор рынка зерна\n\nЭкспортная пошлина на пшеницу: правительство снизило "
            "пошлину, что поддержит маржу экспортёров зерна в четвёртом квартале."
        )
        return OcrPage(page=page, text=text, confidence=0.87, method=self.name)


def scanned_pdf(text: str) -> bytes:
    """An image-only PDF: the page is rendered from a text PDF and re-embedded as a picture."""
    src = FPDF()
    src.add_font("Roboto", fname=str(FONT))
    src.set_font("Roboto", size=16)
    src.add_page()
    src.multi_cell(0, 10, text)
    page = pdfium.PdfDocument(bytes(src.output()))[0]
    img = io.BytesIO()
    page.render(scale=300 / 72).to_pil().save(img, format="PNG")
    out = FPDF()
    out.add_page()
    out.image(img, x=0, y=0, w=out.w)
    return bytes(out.output())


@pytest.fixture(scope="module")
async def env(infra: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_analytics)
    embedder = FakeEmbedding()
    async with pg_connection(infra["DATABASE_URL"]) as conn:
        outcomes = await ingest_directory(conn, store, FakeOcr(), embedder, SYNTH, "synthetic")
        yield {"conn": conn, "store": store, "embedder": embedder, "settings": settings, "outcomes": outcomes}


async def test_ingest_stores_pages_chunks_and_vectors(env: dict[str, Any]) -> None:
    conn = env["conn"]
    assert [o.status for o in env["outcomes"]] == ["ready", "ready"]
    docs = {d["file_name"]: d for d in await list_documents(conn)}
    rate = docs["demo_key_rate_outlook.pdf"]
    assert rate["title"].startswith("ДЕМО") and rate["origin"] == "synthetic"
    assert rate["extraction"] == "text_layer" and rate["ocr_confidence"] is None
    assert rate["pages"] == 2 and rate["chunks"] >= 4 and "key_rate" in rate["topics"]
    row = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE embedding IS NULL) AS no_vec,
                  count(*) FILTER (WHERE tsv = ''::tsvector) AS no_tsv, count(*) AS n
           FROM core.analytics_chunks WHERE document_id = $1""",
        rate["document_id"],
    )
    assert row["n"] == rate["chunks"] and row["no_vec"] == 0 and row["no_tsv"] == 0
    key = await conn.fetchval(
        "SELECT s3_key FROM core.analytics_documents WHERE id = $1", rate["document_id"]
    )
    assert env["store"].get(key).startswith(b"%PDF")


async def test_reingest_is_idempotent_and_force_rebuilds(env: dict[str, Any]) -> None:
    conn, store, emb = env["conn"], env["store"], env["embedder"]
    data = (SYNTH / "demo_steel_market.pdf").read_bytes()
    again = await ingest_pdf(conn, store, FakeOcr(), emb, data, "demo_steel_market.pdf", "synthetic")
    assert again.status == "skipped"
    before = await conn.fetch(
        "SELECT id FROM core.analytics_chunks WHERE document_id = $1::uuid", again.document_id
    )
    forced = await ingest_pdf(
        conn, store, FakeOcr(), emb, data, "demo_steel_market.pdf", "synthetic", force=True
    )
    assert forced.status == "ready" and forced.document_id == again.document_id
    after = await conn.fetch(
        "SELECT id FROM core.analytics_chunks WHERE document_id = $1::uuid", again.document_id
    )
    assert {r["id"] for r in after} == {r["id"] for r in before}, "published citations must stay valid"
    assert await conn.fetchval("SELECT count(*) FROM core.analytics_documents WHERE sha256 = "
                               "(SELECT sha256 FROM core.analytics_documents WHERE id = $1::uuid)",
                               again.document_id) == 1  # fmt: skip


async def test_failed_force_keeps_the_last_ready_index(env: dict[str, Any]) -> None:
    conn, store = env["conn"], env["store"]
    data = (SYNTH / "demo_steel_market.pdf").read_bytes()
    doc = await conn.fetchrow(
        "SELECT * FROM core.analytics_documents WHERE file_name='demo_steel_market.pdf'"
    )
    before = await conn.fetch(
        "SELECT * FROM core.analytics_chunks WHERE document_id=$1 ORDER BY id", doc["id"]
    )

    class UnavailableEmbedding(FakeEmbedding):
        async def embed_documents(self, texts: Any) -> list[list[float]]:
            raise RuntimeError("embedding service unavailable")

    result = await ingest_pdf(
        conn, store, FakeOcr(), UnavailableEmbedding(), data, "renamed.pdf", "synthetic", force=True
    )
    assert result.status == "failed"
    after = await conn.fetchrow("SELECT * FROM core.analytics_documents WHERE id=$1", doc["id"])
    assert after["status"] == "ready" and after["embedding_model"] == doc["embedding_model"]
    assert after["file_name"] == doc["file_name"] and after["error"]
    assert (
        await conn.fetch("SELECT * FROM core.analytics_chunks WHERE document_id=$1 ORDER BY id", doc["id"])
        == before
    )
    hits = await hybrid_search(conn, env["embedder"], "цены на прокат", document_ids=[str(doc["id"])])
    assert hits


async def test_concurrent_imports_converge_to_one_index(env: dict[str, Any], infra: dict[str, str]) -> None:
    data = (SYNTH / "demo_key_rate_outlook.pdf").read_bytes() + b"\n% concurrent-import-test\n"

    async def load() -> Any:
        async with pg_connection(infra["DATABASE_URL"]) as conn:
            return await ingest_pdf(
                conn, env["store"], FakeOcr(), env["embedder"], data, "concurrent.pdf", "synthetic"
            )

    first, second = await asyncio.gather(load(), load())
    assert sorted([first.status, second.status]) == ["ready", "skipped"]
    assert first.document_id == second.document_id
    await env["conn"].execute("DELETE FROM core.analytics_documents WHERE id=$1::uuid", first.document_id)


async def test_empty_scan_is_failed_instead_of_published(env: dict[str, Any]) -> None:
    pdf = FPDF()
    pdf.add_page()
    result = await ingest_pdf(
        env["conn"], env["store"], FakeOcr(), env["embedder"], bytes(pdf.output()), "empty.pdf", "upload"
    )
    assert result.status == "failed" and "no indexable text" in str(result.error)
    assert (
        await env["conn"].fetchval(
            "SELECT status FROM core.analytics_documents WHERE id=$1::uuid", result.document_id
        )
        == "failed"
    )
    await env["conn"].execute("DELETE FROM core.analytics_documents WHERE id=$1::uuid", result.document_id)


async def test_reembed_rolls_back_partial_document_on_failure(env: dict[str, Any], monkeypatch: Any) -> None:
    from app.services import analytics_ingest

    monkeypatch.setattr(analytics_ingest, "EMBED_BATCH", 1)
    conn = env["conn"]
    before = await conn.fetch("SELECT id,embedding FROM core.analytics_chunks ORDER BY id")
    models = await conn.fetch("SELECT id,embedding_model FROM core.analytics_documents ORDER BY id")

    class FailsAfterOneBatch(FakeEmbedding):
        name = "incomplete-model"
        calls = 0

        async def embed_documents(self, texts: Any) -> list[list[float]]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("second batch failed")
            return await super().embed_documents(["different text"] * len(texts))

    with pytest.raises(RuntimeError, match="second batch failed"):
        await reembed(conn, FailsAfterOneBatch())
    assert await conn.fetch("SELECT id,embedding FROM core.analytics_chunks ORDER BY id") == before
    assert await conn.fetch("SELECT id,embedding_model FROM core.analytics_documents ORDER BY id") == models


async def test_hybrid_search_cites_pages_and_matches_contract(env: dict[str, Any]) -> None:
    hits = await hybrid_search(env["conn"], env["embedder"], "прогноз ключевой ставки ЦБ до конца года")
    assert hits, "no hits"
    top = hits[0]
    assert top["doc_title"].startswith("ДЕМО") and "ставк" in top["text"].lower()
    assert top["page"] >= 1 and top["extraction"] == "text_layer"
    assert top["score_breakdown"]["vector_rank"] or top["score_breakdown"]["bm25_rank"]
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    for h in hits:
        _validate_component(h, "tools-api.openapi.yaml", "AnalyticsHit")
    steel = await hybrid_search(env["conn"], env["embedder"], "цены на арматуру и прокат", limit=3)
    assert "сталь" in steel[0]["doc_title"].lower() or "прокат" in steel[0]["text"].lower()


async def test_search_filters(env: dict[str, Any]) -> None:
    conn, emb = env["conn"], env["embedder"]
    docs = {d["file_name"]: d for d in await list_documents(conn)}
    steel_id = str(docs["demo_steel_market.pdf"]["document_id"])
    only = await hybrid_search(conn, emb, "ключевая ставка", document_ids=[steel_id])
    assert only and {h["document_id"] for h in only} == {steel_id}
    assert await hybrid_search(conn, emb, "ключевая ставка", tags=["gold"]) == []
    future = docs["demo_key_rate_outlook.pdf"]["doc_date"].replace(year=2099)
    assert await hybrid_search(conn, emb, "ключевая ставка", date_from=future) == []


@pytest.mark.parametrize("error", [EmbeddingUnavailableError("offline"), httpx.ConnectError("offline")])
async def test_search_keeps_lexical_results_when_embeddings_are_unavailable(
    env: dict[str, Any], error: Exception
) -> None:
    class OfflineEmbedding(FakeEmbedding):
        async def embed_query(self, text: str) -> list[float]:
            raise error

    hits = await hybrid_search(env["conn"], OfflineEmbedding(), "ключевая ставка")
    assert hits and all(hit["score_breakdown"]["vector_rank"] is None for hit in hits)
    assert all(hit["score_breakdown"]["bm25_rank"] is not None for hit in hits)
    assert await hybrid_search(env["conn"], OfflineEmbedding(), "несуществующийтермин") == []


async def test_vectors_of_another_model_are_ignored_until_reembed(env: dict[str, Any]) -> None:
    conn, emb = env["conn"], env["embedder"]

    class OtherModel(FakeEmbedding):
        name = "other-model@1"

    other = OtherModel()
    fts_only = await hybrid_search(conn, other, "ключевая ставка")
    assert fts_only and all(h["score_breakdown"]["vector_rank"] is None for h in fts_only)
    n = await reembed(conn, other)
    assert n > 0
    assert any(h["score_breakdown"]["vector_rank"] for h in await hybrid_search(conn, other, "ставка"))
    await reembed(conn, emb)


async def test_presigned_download_link_points_to_page(env: dict[str, Any]) -> None:
    conn, settings = env["conn"], env["settings"]
    doc = next(d for d in await list_documents(conn) if d["file_name"] == "demo_key_rate_outlook.pdf")
    link = await download_link(conn, settings, doc["document_id"], 2)
    assert link and link["url"].endswith("#page=2") and link["expires_in"] == 900
    # The public endpoint equals the internal one in tests: the presigned URL must be downloadable.
    parsed = urlparse(link["url"])
    async with httpx.AsyncClient() as http:
        resp = await http.get(link["url"].split("#", 1)[0])
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF"), parsed.netloc
    with pytest.raises(ValueError, match="page"):
        await download_link(conn, settings, doc["document_id"], 99)


async def test_scanned_pdf_goes_through_ocr_with_s3_cache(env: dict[str, Any]) -> None:
    conn, store, emb = env["conn"], env["store"], env["embedder"]
    data = scanned_pdf("Сканированный обзор рынка зерна")
    ocr = RecordingOcr()
    first = await ingest_pdf(conn, store, ocr, emb, data, "scan_grain.pdf", "upload")
    assert first.status == "ready" and first.ocr_pages == 1 and first.extraction == "ocr_tesseract"
    assert ocr.calls == 1
    rebuilt = await ingest_pdf(conn, store, ocr, emb, data, "scan_grain.pdf", "upload", force=True)
    assert rebuilt.status == "ready" and ocr.calls == 1  # OCR result reused from S3
    hits = await hybrid_search(conn, emb, "экспортная пошлина на пшеницу", limit=3)
    top = hits[0]
    assert top["document_id"] == first.document_id
    assert top["extraction"] == "ocr_tesseract" and top["ocr_confidence"] == pytest.approx(0.87)
    _validate_component(top, "tools-api.openapi.yaml", "AnalyticsHit")
    await conn.execute("DELETE FROM core.analytics_documents WHERE id = $1::uuid", first.document_id)


def _tesseract_langs() -> list[str]:
    out = subprocess.run([str(TESSERACT), "--list-langs"], capture_output=True, text=True, check=True)
    return out.stdout.split()


@pytest.mark.skipif(TESSERACT is None, reason="tesseract is not installed (it is in the Docker image)")
async def test_real_tesseract_reads_a_scanned_page() -> None:
    lang = "rus" if "rus" in _tesseract_langs() else "eng"
    text = (
        "Ключевая ставка сохранена на уровне 14 процентов" if lang == "rus" else "Key rate kept at 14 percent"
    )
    page = pdfium.PdfDocument(scanned_pdf(text))[0]
    img = io.BytesIO()
    page.render(scale=300 / 72).to_pil().save(img, format="PNG")
    result = await TesseractOcr(languages=lang, binary=str(TESSERACT)).recognize(img.getvalue(), 1)
    assert result.method == "ocr_tesseract" and result.confidence and result.confidence > 0.5
    assert "14" in result.text and ("ставка" in result.text.lower() or "rate" in result.text.lower())
