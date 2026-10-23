"""Bank analytics ingestion (SPEC §5.3): PDF -> S3 -> pages (text layer, else OCR) -> chunks -> embeddings
+ tsvector. Generic for any future material (FAQ #42).

* the file is stored once by sha256 (`analytics/<sha>.pdf`); re-ingesting the same file is a no-op unless
  `force` (then pages/chunks are rebuilt);
* OCR results are cached in S3 (`analytics/ocr/<sha>/<page>.json`), so re-indexing (e.g. a new
  embedding model) never re-runs OCR;
* the document gets status `ready` only after chunks with embeddings are stored.
"""

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.logging import get_logger
from app.core.s3 import ObjectStore
from app.domain.chunking import chunk_pages, document_meta
from app.domain.ports import OcrPage
from app.integrations.embeddings import to_pgvector
from app.integrations.pdf import page_texts, render_png

log = get_logger(__name__)

MIN_TEXT_LAYER_CHARS = 200  # fewer characters on a page -> treat as scanned
EMBED_BATCH = 64


class Ocr(Protocol):
    @property
    def name(self) -> str: ...

    async def recognize(self, image_png: bytes, page: int) -> OcrPage: ...


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class IngestOutcome:
    document_id: str
    file_name: str
    status: str  # ready | skipped | failed
    pages: int = 0
    chunks: int = 0
    extraction: str | None = None
    ocr_pages: int = 0
    error: str | None = None
    title: str | None = None
    topics: list[str] = field(default_factory=list)


async def _ocr_page(store: ObjectStore, ocr: Ocr, sha: str, data: bytes, page: int) -> OcrPage:
    key = f"ocr/{sha}/{page}.{ocr.name}.json"
    try:
        cached = json.loads(await asyncio.to_thread(store.get, key))
        return OcrPage(
            page=page, text=cached["text"], confidence=cached["confidence"], method=cached["method"]
        )
    except Exception as exc:  # cache miss (NoSuchKey) or unreadable cache -> recognize
        log.debug("ocr_cache_miss", key=key, reason=type(exc).__name__)
    png = await asyncio.to_thread(render_png, data, page)
    result = await ocr.recognize(png, page)
    body = json.dumps(
        {"text": result.text, "confidence": result.confidence, "method": result.method}, ensure_ascii=False
    )
    await asyncio.to_thread(store.put, key, body.encode(), "application/json")
    return result


async def extract_pages(store: ObjectStore, ocr: Ocr, sha: str, data: bytes) -> list[OcrPage]:
    pages = await asyncio.to_thread(page_texts, data)
    out: list[OcrPage] = []
    for p in pages:
        if len(p.text.strip()) >= MIN_TEXT_LAYER_CHARS:
            out.append(OcrPage(page=p.number, text=p.text, confidence=None, method="text_layer"))
        else:
            out.append(await _ocr_page(store, ocr, sha, data, p.number))
    return out


def _extraction(methods: set[str]) -> str:
    return methods.pop() if len(methods) == 1 else "mixed"


async def ingest_pdf(
    conn: Any,
    store: ObjectStore,
    ocr: Ocr,
    embedder: Embedder,
    data: bytes,
    file_name: str,
    origin: str,
    force: bool = False,
) -> IngestOutcome:
    sha = hashlib.sha256(data).hexdigest()
    existing = await conn.fetchrow("SELECT id, status FROM core.analytics_documents WHERE sha256 = $1", sha)
    if existing and existing["status"] == "ready" and not force:
        return IngestOutcome(str(existing["id"]), file_name, "skipped")
    s3_key = f"{sha}.pdf"
    await asyncio.to_thread(store.put, s3_key, data, "application/pdf")
    if existing:
        doc_id = existing["id"]
        await conn.execute(
            "UPDATE core.analytics_documents SET status = 'processing', error = NULL, file_name = $2 "
            "WHERE id = $1",
            doc_id,
            file_name,
        )
    else:
        doc_id = await conn.fetchval(
            """INSERT INTO core.analytics_documents (sha256, file_name, s3_key, title, origin)
               VALUES ($1, $2, $3, $2, $4) RETURNING id""",
            sha,
            file_name,
            s3_key,
            origin,
        )
    try:
        pages = await extract_pages(store, ocr, sha, data)
        texts = [(p.page, p.text) for p in pages]
        meta = document_meta(texts, file_name)
        title = (
            meta.title
            if origin != "synthetic" or meta.title.startswith("ДЕМО")
            else f"ДЕМО (синтетика): {meta.title}"
        )
        chunks = chunk_pages(texts)
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            batch = chunks[i : i + EMBED_BATCH]
            vectors.extend(
                await embedder.embed_documents([f"{c.section or ''} {c.text}".strip() for c in batch])
            )
        confs = [p.confidence for p in pages if p.confidence is not None]
        async with conn.transaction():
            await conn.execute("DELETE FROM core.analytics_chunks WHERE document_id = $1", doc_id)
            await conn.execute("DELETE FROM core.analytics_pages WHERE document_id = $1", doc_id)
            await conn.executemany(
                """INSERT INTO core.analytics_pages (document_id, page, text, method, confidence, chars)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                [(doc_id, p.page, p.text, p.method, p.confidence, len(p.text)) for p in pages],
            )
            await conn.executemany(
                """INSERT INTO core.analytics_chunks (document_id, page, chunk_index, section, text,
                       embedding)
                   VALUES ($1, $2, $3, $4, $5, $6::vector)""",
                [
                    (doc_id, c.page, c.index, c.section, c.text, to_pgvector(v))
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
            await conn.execute(
                """UPDATE core.analytics_documents SET title = $2, doc_date = $3, topics = $4, pages = $5,
                       extraction = $6, ocr_confidence = $7, status = 'ready', embedding_model = $8,
                       processed_at = now()
                   WHERE id = $1""",
                doc_id,
                title,
                meta.doc_date,
                meta.topics,
                len(pages),
                _extraction({p.method for p in pages}),
                round(sum(confs) / len(confs), 3) if confs else None,
                embedder.name,
            )
    except Exception as exc:
        await conn.execute(
            "UPDATE core.analytics_documents SET status = 'failed', error = $2 WHERE id = $1",
            doc_id,
            f"{type(exc).__name__}: {exc}"[:1000],
        )
        log.exception("analytics_ingest_failed", file=file_name)
        return IngestOutcome(str(doc_id), file_name, "failed", error=str(exc)[:300])
    outcome = IngestOutcome(
        str(doc_id), file_name, "ready", pages=len(pages), chunks=len(chunks),
        extraction=_extraction({p.method for p in pages}),
        ocr_pages=sum(1 for p in pages if p.method != "text_layer"), title=title, topics=meta.topics,
    )  # fmt: skip
    log.info("analytics_ingested", **{k: v for k, v in outcome.__dict__.items() if k != "topics"})
    return outcome


async def ingest_directory(
    conn: Any,
    store: ObjectStore,
    ocr: Ocr,
    embedder: Embedder,
    directory: Path,
    origin: str,
    force: bool = False,
) -> list[IngestOutcome]:
    outcomes = []
    for path in sorted(await asyncio.to_thread(lambda: list(directory.glob("*.pdf")))):
        data = await asyncio.to_thread(path.read_bytes)
        outcomes.append(await ingest_pdf(conn, store, ocr, embedder, data, path.name, origin, force))
    return outcomes


async def reembed(conn: Any, embedder: Embedder) -> int:
    """Re-index all chunks with the current embedding model (e.g. after switching EMBEDDING_PROVIDER)."""
    rows = await conn.fetch("SELECT id, section, text FROM core.analytics_chunks ORDER BY id")
    for i in range(0, len(rows), EMBED_BATCH):
        batch = rows[i : i + EMBED_BATCH]
        vectors = await embedder.embed_documents([f"{r['section'] or ''} {r['text']}".strip() for r in batch])
        await conn.executemany(
            "UPDATE core.analytics_chunks SET embedding = $2::vector WHERE id = $1",
            [(r["id"], to_pgvector(v)) for r, v in zip(batch, vectors, strict=True)],
        )
    await conn.execute(
        "UPDATE core.analytics_documents SET embedding_model = $1 WHERE status = 'ready'", embedder.name
    )
    return len(rows)
