"""Rebuild materialized company contexts (core.company_context).

Counterparty statistics are computed set-based in SQL (temp tables), features are assembled by the
pure domain function ``build_context``. Used after ingest (all companies) and after a user edits a
profile (a single company). ``user_provided`` is preserved; ``context_version`` is bumped.
"""

import json
from decimal import Decimal
from typing import Any

from app.domain.company_context import CompanyFacts, Counterparty, DirectionStats, build_context
from app.domain.scoring import scoring_features

BATCH = 5000

_CP_CREATE = """
CREATE TEMP TABLE tmp_cp (company_id text, dir text, cp_id text, amount float8, tx_count int, known bool)
ON COMMIT DROP
"""

# CREATE TABLE AS cannot take bind parameters, hence a separate INSERT.
_CP_SQL = """
INSERT INTO tmp_cp
SELECT payer_id AS company_id, 'out'::text AS dir, beneficiary_id AS cp_id, amount_rub::float8 AS amount,
       tx_count, beneficiary_known AS known
FROM core.payments_agg
WHERE NOT is_self_transfer AND payer_known AND ($1::text[] IS NULL OR payer_id = ANY($1::text[]))
UNION ALL
SELECT beneficiary_id, 'in', payer_id, amount_rub::float8, tx_count, payer_known
FROM core.payments_agg
WHERE NOT is_self_transfer AND beneficiary_known AND ($1::text[] IS NULL OR beneficiary_id = ANY($1::text[]))
"""

_STATS_SQL = """
CREATE TEMP TABLE tmp_cp_stats ON COMMIT DROP AS
WITH tot AS (
    SELECT company_id, dir, sum(amount) AS tot, coalesce(sum(amount) FILTER (WHERE known), 0) AS known_amt,
           count(*) FILTER (WHERE known) AS n_known, count(*) FILTER (WHERE NOT known) AS n_unknown
    FROM tmp_cp GROUP BY company_id, dir
),
hhi AS (
    SELECT t.company_id, t.dir, sum(power(t.amount / nullif(x.tot, 0), 2)) * 10000 AS hhi
    FROM tmp_cp t JOIN tot x USING (company_id, dir) GROUP BY t.company_id, t.dir
),
ranked AS (
    SELECT t.*, row_number() OVER (PARTITION BY t.company_id, t.dir ORDER BY t.amount DESC, t.cp_id) AS rn
    FROM tmp_cp t
),
top AS (
    SELECT r.company_id, r.dir,
           jsonb_agg(jsonb_build_object(
               'id', r.cp_id, 'amount', r.amount, 'tx', r.tx_count, 'share', r.amount / nullif(x.tot, 0),
               'known', r.known, 'okved_code', c2.okved_code, 'okved_name', c2.okved_name,
               'tags', coalesce(to_jsonb(o2.industry_tags), '[]'::jsonb), 'region', c2.region,
               'same_group', (c2.group_id IS NOT NULL AND c2.group_id = c1.group_id)
           ) ORDER BY r.rn) AS top
    FROM ranked r
    JOIN tot x USING (company_id, dir)
    JOIN core.companies c1 ON c1.id = r.company_id
    LEFT JOIN core.companies c2 ON c2.id = r.cp_id AND r.known
    LEFT JOIN core.okved_dict o2 ON o2.code = c2.okved_code
    WHERE r.rn <= 10
    GROUP BY r.company_id, r.dir
),
shares AS (
    SELECT s.company_id, s.dir, jsonb_object_agg(s.tag, s.share) AS shares
    FROM (
        SELECT t.company_id, t.dir, tag, sum(t.amount) / max(x.tot) AS share
        FROM tmp_cp t
        JOIN tot x USING (company_id, dir)
        JOIN core.companies c ON c.id = t.cp_id
        JOIN core.okved_dict o ON o.code = c.okved_code
        CROSS JOIN LATERAL unnest(o.industry_tags) AS tag
        WHERE t.known
        GROUP BY t.company_id, t.dir, tag
    ) s
    GROUP BY s.company_id, s.dir
)
SELECT x.company_id, x.dir, x.tot, x.known_amt, x.n_known, x.n_unknown, h.hhi, tp.top, sh.shares
FROM tot x
LEFT JOIN hhi h USING (company_id, dir)
LEFT JOIN top tp USING (company_id, dir)
LEFT JOIN shares sh USING (company_id, dir)
"""

_FACTS_SQL = """
SELECT c.*, o.section AS okved_section, o.section_name AS okved_section_name,
       coalesce(o.industry_tags, '{}') AS industry_tags, coalesce(o.commodities, '{}') AS okved_commodities,
       coalesce(o.consumer_demand, false) AS f_consumer_demand,
       coalesce(o.import_dependent, false) AS f_import_dependent,
       coalesce(o.export_oriented, false) AS f_export_oriented,
       coalesce(o.rate_sensitive_industry, false) AS f_rate_sensitive_industry,
       g.valid_size AS group_size, g.revenue_rub AS group_revenue_rub, g.client_count AS group_client_count,
       coalesce((SELECT array_agg(p.product ORDER BY p.product) FROM core.company_products p
                 WHERE p.company_id = c.id), '{}') AS products,
       coalesce((SELECT array_agg(f.country ORDER BY f.country) FROM core.company_fts_countries f
                 WHERE f.company_id = c.id), '{}') AS fts_countries,
       si.tot AS in_tot, si.known_amt AS in_known, si.n_known AS in_n_known, si.n_unknown AS in_n_unknown,
       si.hhi AS in_hhi, si.top AS in_top, si.shares AS in_shares,
       so.tot AS out_tot, so.known_amt AS out_known, so.n_known AS out_n_known, so.n_unknown AS out_n_unknown,
       so.hhi AS out_hhi, so.top AS out_top, so.shares AS out_shares,
       coalesce((SELECT sum(amount_rub) FROM core.payments_agg pa
                 WHERE pa.payer_id = c.id AND pa.is_self_transfer), 0) AS self_transfer_rub,
       coalesce((SELECT array_agg(x.other ORDER BY x.w DESC, x.other) FROM (
                    SELECT CASE WHEN e.src_id = c.id THEN e.dst_id ELSE e.src_id END AS other, e.weight AS w
                    FROM core.company_edges e
                    WHERE e.edge_type = 'shared_person' AND (e.src_id = c.id OR e.dst_id = c.id)) x),
                '{}') AS shared_person_companies,
       ctx.user_provided AS user_provided
FROM core.companies c
LEFT JOIN core.okved_dict o ON o.code = c.okved_code
LEFT JOIN core.company_groups g ON g.id = c.group_id
LEFT JOIN tmp_cp_stats si ON si.company_id = c.id AND si.dir = 'in'
LEFT JOIN tmp_cp_stats so ON so.company_id = c.id AND so.dir = 'out'
LEFT JOIN core.company_context ctx ON ctx.company_id = c.id
WHERE c.is_valid AND c.id = ANY($1::text[])
"""


def _loads(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


def _direction(r: Any, prefix: str) -> DirectionStats:
    total = r[f"{prefix}_tot"]
    if total is None:
        return DirectionStats()
    top = tuple(
        Counterparty(
            counterparty_id=x["id"],
            amount_rub=float(x["amount"]),
            tx_count=int(x["tx"]),
            share=float(x["share"] or 0),
            known=bool(x["known"]),
            okved_code=x.get("okved_code"),
            okved_name=x.get("okved_name"),
            industry_tags=tuple(x.get("tags") or ()),
            region=x.get("region"),
            same_group=bool(x.get("same_group")),
        )
        for x in (_loads(r[f"{prefix}_top"]) or [])
    )
    return DirectionStats(
        total_rub=float(total),
        known_rub=float(r[f"{prefix}_known"] or 0),
        n_known=int(r[f"{prefix}_n_known"] or 0),
        n_unknown=int(r[f"{prefix}_n_unknown"] or 0),
        hhi=float(r[f"{prefix}_hhi"]) if r[f"{prefix}_hhi"] is not None else None,
        top=top,
        industry_shares={k: float(v) for k, v in (_loads(r[f"{prefix}_shares"]) or {}).items()},
    )


def facts_from_row(r: Any) -> CompanyFacts:
    group_revenue: Decimal | None = r["group_revenue_rub"]
    return CompanyFacts(
        company_id=r["id"],
        is_valid=r["is_valid"],
        client_type=r["client_type"],
        okved_code=r["okved_code"],
        okved_name=r["okved_name"],
        okved_section=r["okved_section"],
        okved_section_name=r["okved_section_name"],
        industry_tags=tuple(r["industry_tags"]),
        okved_commodities=tuple(r["okved_commodities"]),
        okved_flags={
            "consumer_demand": r["f_consumer_demand"],
            "import_dependent": r["f_import_dependent"],
            "export_oriented": r["f_export_oriented"],
            "rate_sensitive_industry": r["f_rate_sensitive_industry"],
        },
        region=r["region"],
        city=r["city"],
        age_bucket=r["age_bucket"],
        staff_bucket=r["staff_bucket"],
        eco_stage=r["eco_stage"],
        okfs_name=r["okfs_name"],
        foreign_ownership=r["foreign_ownership"],
        owner_info_reliable=r["owner_info_reliable"],
        revenue_rub=r["revenue_rub"],
        revenue_stub=r["revenue_stub"],
        group_id=r["group_id"],
        group_size=r["group_size"],
        group_revenue_rub=group_revenue,
        group_client_count=r["group_client_count"],
        products=tuple(r["products"]),
        fts_flag=r["fts_flag"],
        fts_countries=tuple(r["fts_countries"]),
        incoming=_direction(r, "in"),
        outgoing=_direction(r, "out"),
        self_transfer_rub=float(r["self_transfer_rub"]),
        shared_person_companies=tuple(r["shared_person_companies"]),
        user_provided=_loads(r["user_provided"]) or {},
    )


_CTX_COLUMNS = [
    "company_id", "features", "completeness", "scoring_features", "client_type", "okved_section", "region",
    "segment",
    "industry_tags", "counterparty_tags", "counterparty_tags_any", "fts_countries", "commodities", "products",
    "rate_sensitive",
    "fx_sensitive", "completeness_score",
]  # fmt: skip


async def rebuild_contexts(conn: Any, company_ids: list[str] | None) -> dict[str, int]:
    """Recompute contexts for the given companies (None = all valid companies)."""
    async with conn.transaction():
        await conn.execute(_CP_CREATE)
        await conn.execute(_CP_SQL, company_ids)
        await conn.execute("CREATE INDEX ON tmp_cp (company_id, dir)")
        await conn.execute("ANALYZE tmp_cp")
        await conn.execute(_STATS_SQL)
        await conn.execute("CREATE INDEX ON tmp_cp_stats (company_id, dir)")
        await conn.execute("ANALYZE tmp_cp_stats")
        if company_ids is None:
            ids = [
                r["id"] for r in await conn.fetch("SELECT id FROM core.companies WHERE is_valid ORDER BY id")
            ]
        else:
            ids = list(company_ids)
        await conn.execute(
            "CREATE TEMP TABLE tmp_ctx (company_id text, features text, completeness text,"
            " scoring_features text, client_type text,"
            " okved_section text, region text, segment text, industry_tags text[], counterparty_tags text[],"
            " counterparty_tags_any text[],"
            " fts_countries text[], commodities text[], products text[], rate_sensitive bool,"
            " fx_sensitive bool,"
            " completeness_score float8) ON COMMIT DROP"
        )
        built = 0
        for i in range(0, len(ids), BATCH):
            rows = await conn.fetch(_FACTS_SQL, ids[i : i + BATCH])
            records = []
            for r in rows:
                res = build_context(facts_from_row(r))
                ix = res.index
                records.append(
                    (
                        r["id"],
                        json.dumps(res.features, ensure_ascii=False),
                        json.dumps(res.completeness, ensure_ascii=False),
                        json.dumps(scoring_features(res.features), ensure_ascii=False),
                        ix["client_type"], ix["okved_section"], ix["region"], ix["segment"],
                        ix["industry_tags"], ix["counterparty_tags"], ix["counterparty_tags_any"],
                        ix["fts_countries"], ix["commodities"],
                        ix["products"], ix["rate_sensitive"], ix["fx_sensitive"], ix["completeness_score"],
                    )
                )  # fmt: skip
            if records:
                await conn.copy_records_to_table("tmp_ctx", records=records, columns=_CTX_COLUMNS)
                built += len(records)
        sets = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in _CTX_COLUMNS if c not in ("company_id", "features", "completeness")
        )
        # column lists are module constants, never user input
        await conn.execute(
            f"""INSERT INTO core.company_context ({", ".join(_CTX_COLUMNS)})
                SELECT company_id, features::jsonb, completeness::jsonb, scoring_features::jsonb,
                       {", ".join(_CTX_COLUMNS[4:])}
                FROM tmp_ctx
                ON CONFLICT (company_id) DO UPDATE SET
                    context_version = core.company_context.context_version + 1,
                    features = EXCLUDED.features, completeness = EXCLUDED.completeness, {sets},
                    computed_at = now()"""  # noqa: S608
        )
        removed = await conn.execute(
            """DELETE FROM core.company_context ctx USING core.companies c
               WHERE c.id = ctx.company_id AND NOT c.is_valid"""
        )
    return {"built": built, "removed": int(str(removed).split()[-1])}
