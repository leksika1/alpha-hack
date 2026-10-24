"""Bank analytics uploaded by an admin (FR-A7): the PDF is stored and registered at once, the worker
indexes it.

The file goes to S3 under the same key the ingestion uses (`<sha256>.pdf`) and the document gets status
`processing`, so the admin sees it immediately; the task then runs the usual pipeline (text layer or OCR ->
chunks -> embeddings). Re-uploading a ready file changes nothing; a failed one is queued again.
"""

import asyncio
import hashlib
import uuid
from typing import Any

from app.core.config import Settings
from app.core.s3 import S3ObjectStore, get_s3_client
from app.integrations.embeddings import get_embedder
from app.integrations.ocr import get_ocr
from app.integrations.pdf import validate_pdf
from app.services.analytics_ingest import IngestOutcome, ingest_pdf

ORIGIN = "upload"


async def register_upload(conn: Any, settings: Settings, data: bytes, file_name: str) -> dict[str, Any]:
    """Stores the PDF and its document row; `queued` tells whether the worker has to index it."""
    validate_pdf(data, settings.analytics_max_bytes)
    sha = hashlib.sha256(data).hexdigest()
    s3_key = f"{sha}.pdf"
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_analytics)
    await asyncio.to_thread(store.put, s3_key, data, "application/pdf")
    row = await conn.fetchrow(
        """INSERT INTO core.analytics_documents (sha256, file_name, s3_key, title, origin)
           VALUES ($1, $2, $3, $2, $4)
           ON CONFLICT (sha256) DO UPDATE SET
               status = CASE WHEN core.analytics_documents.status = 'ready' THEN 'ready'
                             ELSE 'processing' END,
               error = CASE WHEN core.analytics_documents.status = 'ready'
                            THEN core.analytics_documents.error END
           RETURNING id, status, title, (xmax = 0) AS created""",
        sha,
        file_name,
        s3_key,
        ORIGIN,
    )
    return {
        "document_id": row["id"],
        "file_name": file_name,
        "title": row["title"],
        "status": row["status"],
        "queued": row["status"] != "ready",
        "duplicate": not row["created"],
    }


async def process_upload(conn: Any, settings: Settings, document_id: uuid.UUID) -> IngestOutcome:
    """Worker side: index a registered upload with the usual pipeline."""
    doc = await conn.fetchrow(
        "SELECT s3_key, file_name, status FROM core.analytics_documents WHERE id = $1", document_id
    )
    if doc is None:
        return IngestOutcome(str(document_id), "", "failed", error="document not found")
    if doc["status"] == "ready":
        return IngestOutcome(str(document_id), doc["file_name"], "skipped")
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_analytics)
    data = await asyncio.to_thread(store.get, doc["s3_key"])
    return await ingest_pdf(
        conn, store, get_ocr(settings.ocr_provider), get_embedder(settings), data, doc["file_name"], ORIGIN
    )


async def list_all_documents(conn: Any) -> list[dict[str, Any]]:
    """Every document with its processing state (users see only the ready ones)."""
    rows = await conn.fetch(
        """SELECT d.id, d.title, d.file_name, d.origin, d.status, d.error, d.pages, d.extraction,
                  d.ocr_confidence, d.created_at, d.processed_at,
                  (SELECT count(*) FROM core.analytics_chunks c WHERE c.document_id = d.id) AS chunks
           FROM core.analytics_documents d ORDER BY d.created_at DESC"""
    )
    return [{"document_id": d.pop("id"), **d} for d in map(dict, rows)]
