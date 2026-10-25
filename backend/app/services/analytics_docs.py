"""Bank analytics documents for users: catalog and links to the original PDF page (SPEC FR-A6).

The PDF is served by the API itself (ADR-033): the link works wherever the API does (through the frontend's
nginx on the same domain), and S3 is never exposed to browsers.
"""

import uuid
from collections.abc import Iterator
from typing import Any

from app.core.config import Settings
from app.core.s3 import get_s3_client
from app.core.security import TokenError, decode_token, issue_document_token

LINK_TTL_S = 900
CHUNK_BYTES = 64 * 1024


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
    token = issue_document_token(settings, str(doc["id"]), LINK_TTL_S)
    url = f"/api/v1/analytics/documents/{doc['id']}/file?token={token}"
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


async def document_for_token(
    conn: Any, settings: Settings, document_id: uuid.UUID, token: str
) -> dict[str, Any] | None:
    """The document a link token opens; TokenError if the token is invalid, expired or for another doc."""
    claims = decode_token(settings, token, "doc")
    if claims.get("doc") != str(document_id):
        raise TokenError("token is for another document")
    row = await conn.fetchrow(
        "SELECT s3_key, file_name FROM core.analytics_documents WHERE id = $1 AND status = 'ready'",
        document_id,
    )
    return dict(row) if row else None


def iter_pdf(settings: Settings, s3_key: str) -> Iterator[bytes]:
    """Streams the PDF from S3 in chunks (sync boto3: run it in a thread pool)."""
    body = get_s3_client().get_object(Bucket=settings.s3_bucket_analytics, Key=s3_key)["Body"]
    try:
        yield from body.iter_chunks(CHUNK_BYTES)
    finally:
        body.close()
