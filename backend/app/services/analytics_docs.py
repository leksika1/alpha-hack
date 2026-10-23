"""Bank analytics documents for users: catalog and links to the original PDF page (SPEC FR-A6)."""

import asyncio
import uuid
from typing import Any

from app.core.config import Settings
from app.core.s3 import get_public_s3_client, presigned_get

LINK_TTL_S = 900


async def list_documents(conn: Any) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT d.*, (SELECT count(*) FROM core.analytics_chunks c WHERE c.document_id = d.id) AS chunks
           FROM core.analytics_documents d WHERE d.status = 'ready'
           ORDER BY d.doc_date DESC NULLS LAST, d.title"""
    )
    return [
        {
            "document_id": r["id"],
            "title": r["title"],
            "file_name": r["file_name"],
            "doc_date": r["doc_date"],
            "topics": list(r["topics"]),
            "pages": r["pages"],
            "chunks": r["chunks"],
            "extraction": r["extraction"],
            "ocr_confidence": r["ocr_confidence"],
            "origin": r["origin"],
        }
        for r in rows
    ]


async def download_link(
    conn: Any, settings: Settings, document_id: uuid.UUID, page: int | None
) -> dict[str, Any] | None:
    doc = await conn.fetchrow(
        "SELECT * FROM core.analytics_documents WHERE id = $1 AND status = 'ready'", document_id
    )
    if doc is None:
        return None
    if page is not None and not 1 <= page <= doc["pages"]:
        raise ValueError(f"page must be within 1..{doc['pages']}")
    url = await asyncio.to_thread(
        presigned_get, get_public_s3_client(), settings.s3_bucket_analytics, doc["s3_key"], LINK_TTL_S,
        doc["file_name"],
    )  # fmt: skip
    if page:
        url += f"#page={page}"
    return {
        "document_id": doc["id"],
        "title": doc["title"],
        "url": url,
        "page": page,
        "expires_in": LINK_TTL_S,
    }


async def chunk_link(conn: Any, settings: Settings, chunk_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT document_id, page FROM core.analytics_chunks WHERE id = $1::uuid", chunk_id
    )
    if row is None:
        return None
    return await download_link(conn, settings, row["document_id"], row["page"])
