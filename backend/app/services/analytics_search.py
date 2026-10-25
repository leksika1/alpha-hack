"""Hybrid search over bank analytics (SPEC FR-A5): vector top-k (pgvector, cosine) + full-text top-k
(tsvector 'russian', OR over query lexemes, ts_rank_cd) fused with Reciprocal Rank Fusion.
Output follows contracts/tools-api.openapi.yaml#/components/schemas/AnalyticsHit."""

from datetime import date
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.domain.vectors import EmbeddingResponseError
from app.integrations.embeddings import EmbeddingUnavailableError, to_pgvector

RRF_K = 60
CANDIDATES = 50
log = get_logger(__name__)


class QueryEmbedder(Protocol):
    @property
    def name(self) -> str: ...

    async def embed_query(self, text: str) -> list[float]: ...


# Only module constants (CANDIDATES, RRF_K) are interpolated; all user input is bound as parameters.
_SQL = f"""
WITH docs AS (
    SELECT id, title, doc_date, embedding_model FROM core.analytics_documents
    WHERE status = 'ready'
      AND ($3::date IS NULL OR doc_date IS NULL OR doc_date >= $3)
      AND (cardinality($4::text[]) = 0 OR topics && $4::text[])
      AND (cardinality($5::uuid[]) = 0 OR id = ANY($5::uuid[]))
),
q AS (
    SELECT CASE WHEN plainto_tsquery('russian', $2)::text = '' THEN NULL
                ELSE replace(plainto_tsquery('russian', $2)::text, '&', '|')::tsquery END AS tsq
),
vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> $1::vector, c.id) AS rnk
    FROM core.analytics_chunks c JOIN docs d ON d.id = c.document_id
    WHERE $1::vector IS NOT NULL AND c.embedding IS NOT NULL AND d.embedding_model = $7
    ORDER BY c.embedding <=> $1::vector, c.id
    LIMIT {CANDIDATES}
),
fts AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id) AS rnk
    FROM core.analytics_chunks c JOIN docs d ON d.id = c.document_id, q
    WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq
    ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id
    LIMIT {CANDIDATES}
),
fused AS (
    SELECT coalesce(vec.id, fts.id) AS id, vec.rnk AS vector_rank, fts.rnk AS bm25_rank,
           coalesce(1.0 / ({RRF_K} + vec.rnk), 0) + coalesce(1.0 / ({RRF_K} + fts.rnk), 0) AS rrf
    FROM vec FULL OUTER JOIN fts ON vec.id = fts.id
)
SELECT f.*, c.document_id, c.page, c.section, c.text, d.title, d.doc_date, p.method, p.confidence
FROM fused f JOIN core.analytics_chunks c ON c.id = f.id JOIN docs d ON d.id = c.document_id
LEFT JOIN core.analytics_pages p ON p.document_id = c.document_id AND p.page = c.page
ORDER BY f.rrf DESC, f.id
LIMIT $6
"""  # noqa: S608


def _extraction(method: str | None) -> str:
    """Page extraction method mapped to the contract enum."""
    if method in (None, "text_layer"):
        return "text_layer"
    return "ocr_vlm" if method == "ocr_vlm" else "ocr_tesseract"


async def hybrid_search(
    conn: Any,
    embedder: QueryEmbedder,
    query: str,
    limit: int = 6,
    date_from: date | None = None,
    tags: list[str] | None = None,
    document_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        vector: str | None = to_pgvector(await embedder.embed_query(query))
    except (EmbeddingUnavailableError, EmbeddingResponseError, httpx.HTTPError) as exc:
        log.warning("analytics_lexical_fallback", model=embedder.name, error=type(exc).__name__)
        vector = None
    rows = await conn.fetch(
        _SQL, vector, query, date_from, tags or [], document_ids or [], limit, embedder.name
    )
    return [
        {
            "chunk_id": str(r["id"]),
            "document_id": str(r["document_id"]),
            "doc_title": r["title"],
            "doc_date": r["doc_date"].isoformat() if r["doc_date"] else None,
            "page": r["page"],
            "section": r["section"],
            "text": r["text"],
            "extraction": _extraction(r["method"]),
            "ocr_confidence": r["confidence"],
            "score": round(float(r["rrf"]), 5),
            "score_breakdown": {
                "vector_rank": r["vector_rank"],
                "bm25_rank": r["bm25_rank"],
                "rrf": round(float(r["rrf"]), 5),
            },
        }
        for r in rows
    ]
