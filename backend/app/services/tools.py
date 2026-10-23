"""Read-only data access behind the Tools API (contracts/tools-api.openapi.yaml).

Responses follow the contract component schemas. News texts are returned as untrusted data
({"trust": "untrusted", "content": ...}) and truncated; natural persons are never exposed.
"""

import json
from datetime import date
from typing import Any

from app.services.analytics_search import QueryEmbedder, hybrid_search

SECTION_PREFIXES: dict[str, tuple[str, ...]] = {
    "identity": (
        "okved.",
        "industry_tags",
        "region",
        "city",
        "age_bucket",
        "staff_bucket",
        "eco_stage",
        "ownership_form",
        "foreign_ownership",
        "owner_info_reliable",
        "client_type",
    ),
    "scale": ("revenue_rub", "group.", "segment"),
    "products": ("products.",),
    "sensitivities": ("sensitivities.",),
    "fts": ("fts_countries",),
    "counterparties": ("counterparties.",),
    "links": ("links.",),
    "user_provided": ("user_provided.",),
}


def untrusted(text: str | None, max_chars: int, flags: list[str] | None = None) -> dict[str, Any]:
    content = text or ""
    return {
        "trust": "untrusted",
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
        "flags": list(flags or []),
    }


def _in_sections(path: str, sections: list[str]) -> bool:
    return any(path == p or path.startswith(p) for s in sections for p in SECTION_PREFIXES.get(s, ()))


async def company_context(
    conn: Any, company_id: str, sections: list[str] | None = None
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """SELECT ctx.*, c.is_valid FROM core.company_context ctx JOIN core.companies c ON
               c.id = ctx.company_id
           WHERE ctx.company_id = $1""",
        company_id,
    )
    if row is None:
        return None
    features: dict[str, Any] = json.loads(row["features"])
    wanted = [s for s in (sections or []) if s != "completeness"]
    if wanted:
        features = {k: v for k, v in features.items() if _in_sections(k, wanted)}
    return {
        "company_id": company_id,
        "context_version": row["context_version"],
        "is_valid": row["is_valid"],
        "features": features,
        "completeness": json.loads(row["completeness"]),
    }


async def group(conn: Any, company_id: str, members_limit: int) -> dict[str, Any] | None:
    g = await conn.fetchrow(
        """SELECT g.* FROM core.company_groups g JOIN core.companies c ON
               c.group_id = g.id WHERE c.id = $1""",
        company_id,
    )
    if g is None:
        return None
    members = await conn.fetch(
        """SELECT id, okved_code, okved_name, revenue_rub, region, client_type = 'CLIENT' AS is_client
           FROM core.companies WHERE group_id = $1 AND is_valid
           ORDER BY revenue_rub DESC NULLS LAST, id LIMIT $2""",
        g["id"],
        members_limit,
    )
    return {
        "group_id": g["id"],
        "size": g["valid_size"],
        "revenue_rub": float(g["revenue_rub"]) if g["revenue_rub"] is not None else None,
        "segment": g["segment"],
        "industries": json.loads(g["industries"]),
        "members": [
            {
                "company_id": m["id"],
                "okved_code": m["okved_code"],
                "okved_name": m["okved_name"],
                "revenue_rub": float(m["revenue_rub"]) if m["revenue_rub"] is not None else None,
                "region": m["region"],
                "is_client": m["is_client"],
            }
            for m in members
        ],
    }


_CP_SQL = """
WITH flows AS (
    SELECT 'out'::text AS direction, beneficiary_id AS cp_id, beneficiary_known AS known, amount_rub, tx_count
    FROM core.payments_agg WHERE payer_id = $1 AND NOT is_self_transfer
    UNION ALL
    SELECT 'in', payer_id, payer_known, amount_rub, tx_count
    FROM core.payments_agg WHERE beneficiary_id = $1 AND NOT is_self_transfer
), tot AS (SELECT direction, sum(amount_rub) AS tot FROM flows GROUP BY direction)
SELECT f.direction, f.cp_id, f.known, f.amount_rub, f.tx_count, f.amount_rub / nullif(t.tot, 0) AS share,
       c.okved_code, c.okved_name, c.region, coalesce(o.industry_tags, '{}') AS industry_tags,
       (c.group_id IS NOT NULL AND c.group_id = me.group_id) AS same_group,
       h.label AS hyp_label, h.confidence AS hyp_confidence, h.evidence AS hyp_evidence
FROM flows f
JOIN tot t USING (direction)
JOIN core.companies me ON me.id = $1
LEFT JOIN core.companies c ON c.id = f.cp_id AND f.known
LEFT JOIN core.okved_dict o ON o.code = c.okved_code
LEFT JOIN LATERAL (
    SELECT e.label, e.confidence, e.evidence FROM core.company_edges e
    WHERE e.is_hypothesis AND e.edge_type = 'hypothesis'
      AND ((f.direction = 'out' AND e.src_id = $1 AND e.dst_id = f.cp_id AND e.label = 'key_supplier')
        OR (f.direction = 'in' AND e.src_id = f.cp_id AND e.dst_id = $1 AND e.label = 'key_customer'))
    LIMIT 1
) h ON true
WHERE ($2::text = 'both' OR f.direction = $2) AND ($3::text IS NULL OR $3 = ANY(o.industry_tags))
ORDER BY f.amount_rub DESC, f.cp_id
LIMIT $4
"""


async def counterparties(
    conn: Any, company_id: str, direction: str, limit: int, industry_tag: str | None
) -> dict[str, Any]:
    rows = await conn.fetch(_CP_SQL, company_id, direction, industry_tag, limit)
    ctx = await conn.fetchval("SELECT features FROM core.company_context WHERE company_id = $1", company_id)
    feats: dict[str, Any] = json.loads(ctx) if ctx else {}

    def fv(path: str) -> Any:
        return (feats.get(path) or {}).get("value")

    items = []
    for r in rows:
        hyp = None
        if r["hyp_label"]:
            evidence = json.loads(r["hyp_evidence"])
            hyp = {
                "label": r["hyp_label"],
                "confidence": r["hyp_confidence"],
                "evidence": [evidence.get("statement", ""), *evidence.get("basis", [])],
            }
        items.append(
            {
                "counterparty_id": r["cp_id"],
                "known": r["known"],
                "direction": r["direction"],
                "amount_rub": float(r["amount_rub"]),
                "tx_count": r["tx_count"],
                "share": round(float(r["share"] or 0), 4),
                "okved_code": r["okved_code"],
                "okved_name": r["okved_name"],
                "industry_tags": list(r["industry_tags"]),
                "region": r["region"],
                "same_group": bool(r["same_group"]),
                "relation_hypothesis": hyp,
            }
        )
    return {
        "company_id": company_id,
        "totals": {
            "in_amount_rub": fv("counterparties.amount_in_rub"),
            "out_amount_rub": fv("counterparties.amount_out_rub"),
            "unknown_share_in": fv("counterparties.unknown_share_in"),
            "unknown_share_out": fv("counterparties.unknown_share_out"),
            "hhi_in": fv("counterparties.hhi_in"),
            "hhi_out": fv("counterparties.hhi_out"),
        },
        "items": items,
    }


async def event(conn: Any, event_id: str, max_chars: int) -> dict[str, Any] | None:
    e = await conn.fetchrow("SELECT * FROM core.events WHERE id = $1::uuid", event_id)
    if e is None:
        return None
    news = await conn.fetch(
        """SELECT n.* FROM core.news_items n JOIN core.event_news en ON en.news_id = n.id
           WHERE en.event_id = $1 ORDER BY n.published_at, n.id""",
        e["id"],
    )
    tags = json.loads(e["tags"])
    return {
        "event_id": str(e["id"]),
        "title": e["title"],
        "summary": e["summary"],
        "event_type": e["event_type"],
        "direction": e["direction"],
        "urgent": e["urgent"],
        "urgency_reasons": list(e["urgency_reasons"]),
        "first_seen_at": e["first_seen_at"].isoformat(),
        "tags": {
            "industry_tags": list(e["industry_tags"]),
            "countries": list(e["countries"]),
            "commodities": list(e["commodities"]),
            "regions": list(e["regions"]),
            "entities": [],
            "currencies": tags.get("currencies", []),
            "numbers": tags.get("numbers", []),
        },
        "news": [
            {
                "news_id": str(n["id"]),
                "title": n["title"],
                "url": n["url"],
                "publisher": n["publisher"] or n["source_id"],
                "published_at": n["published_at"].isoformat(),
                "body": untrusted(n["body"], max_chars, list(n["flags"])),
            }
            for n in news
        ],
    }


async def search_news(conn: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Phase 2: Russian full-text search with filters. Phase 4 adds vector search + RRF (hybrid)."""
    rows = await conn.fetch(
        """SELECT n.*, en.event_id,
                  ts_rank_cd(to_tsvector('russian', n.title || ' ' || coalesce(n.body, '')),
                             plainto_tsquery('russian', $1)) AS rank
           FROM core.news_items n LEFT JOIN core.event_news en ON en.news_id = n.id
           WHERE to_tsvector('russian', n.title || ' ' || coalesce(n.body, '')) @@
               plainto_tsquery('russian', $1)
             AND ($2::timestamptz IS NULL OR n.published_at >= $2)
             AND ($3::timestamptz IS NULL OR n.published_at <= $3)
             AND (cardinality($4::text[]) = 0 OR n.event_type = ANY($4::text[]))
             AND (cardinality($5::text[]) = 0 OR (n.tags->'industry_tags') ?| $5::text[])
             AND (cardinality($6::text[]) = 0 OR (n.tags->'countries') ?| $6::text[])
           ORDER BY rank DESC, n.published_at DESC LIMIT $7""",
        args["query"],
        args.get("date_from"),
        args.get("date_to"),
        args.get("event_types") or [],
        args.get("industry_tags") or [],
        args.get("countries") or [],
        int(args.get("limit", 10)),
    )
    max_chars = int(args.get("max_chars", 1500))
    return [
        {
            "news_id": str(r["id"]),
            "event_id": str(r["event_id"]) if r["event_id"] else None,
            "title": r["title"],
            "snippet": untrusted(r["body"], max_chars, list(r["flags"])),
            "published_at": r["published_at"].isoformat(),
            "source": r["publisher"] or r["source_id"],
            "url": r["url"],
            "event_type": r["event_type"],
            "tags": {k: v for k, v in json.loads(r["tags"]).items() if isinstance(v, list)},
            "score": round(float(r["rank"]), 4),
        }
        for r in rows
    ]


async def search_bank_analytics(
    conn: Any, args: dict[str, Any], embedder: QueryEmbedder
) -> list[dict[str, Any]]:
    """Hybrid RAG over bank analytics with quotes and pages (app/services/analytics_search.py)."""
    date_from = args.get("date_from")
    return await hybrid_search(
        conn,
        embedder,
        str(args["query"])[:500],
        limit=min(int(args.get("limit", 6)), 20),
        date_from=date.fromisoformat(date_from) if date_from else None,
        tags=list(args.get("tags") or []),
        document_ids=[str(d) for d in args.get("document_ids") or []],
    )


async def bank_products(conn: Any, tags: list[str]) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT * FROM core.bank_products
           WHERE cardinality($1::text[]) = 0 OR tags && $1::text[] ORDER BY id""",
        tags,
    )
    return [
        {
            "product_id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "tags": list(r["tags"]),
            "url": r["url"],
            "source": r["source"],
        }
        for r in rows
    ]


async def audit(
    conn: Any,
    run_id: str | None,
    tool: str,
    company_id: str | None,
    args: dict[str, Any],
    preview: str | None,
    status_code: int,
    latency_ms: int,
) -> None:
    await conn.execute(
        """INSERT INTO core.tool_audit_log (run_id, tool, company_id, args, result_preview, status_code,
               latency_ms)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)""",
        run_id,
        tool,
        company_id,
        json.dumps(args, ensure_ascii=False, default=str),
        (preview or "")[:1000],
        status_code,
        latency_ms,
    )
