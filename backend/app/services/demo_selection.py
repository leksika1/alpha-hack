"""Pick demo companies: CLIENTs with rich context (credit, foreign trade, group, many counterparties)
from different industries. Deterministic for a given database state."""

from typing import Any

# Industry families for the demo (one company per family); the first matching tag wins.
FAMILIES: dict[str, list[str]] = {
    "metals": ["metals", "metallurgy", "steel"],
    "food_retail": ["retail", "food"],
    "logistics": ["logistics", "trucking", "warehousing"],
    "agro": ["agriculture", "grain_trade"],
    "construction": ["construction", "developer"],
    "wholesale_import": ["wholesale"],
}

_SQL = """
SELECT ctx.company_id, ctx.industry_tags, ctx.okved_section, ctx.region, ctx.segment, ctx.fts_countries,
       ctx.products, ctx.rate_sensitive, ctx.fx_sensitive, ctx.completeness_score,
       (ctx.features->'counterparties.n_in'->>'value')::int
         + (ctx.features->'counterparties.n_out'->>'value')::int AS n_cp,
       coalesce((ctx.features->'group.size'->>'value')::int, 1) AS group_size,
       c.okved_code, c.okved_name, c.revenue_rub
FROM core.company_context ctx
JOIN core.companies c ON c.id = ctx.company_id
WHERE ctx.client_type = 'CLIENT' AND ctx.industry_tags && $1::text[] AND ctx.completeness_score >= 0.85
ORDER BY (ctx.rate_sensitive::int + ctx.fx_sensitive::int
          + (coalesce((ctx.features->'group.size'->>'value')::int, 1) > 1)::int) DESC,
         (ctx.features->'counterparties.n_in'->>'value')::int
           + (ctx.features->'counterparties.n_out'->>'value')::int DESC,
         ctx.company_id
LIMIT 20
"""


async def select_demo_companies(conn: Any, per_family: int = 1) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    used: set[str] = set()
    for family, tags in FAMILIES.items():
        rows = await conn.fetch(_SQL, tags)
        picked = 0
        for r in rows:
            if r["company_id"] in used:
                continue
            used.add(r["company_id"])
            chosen.append(
                {
                    "family": family,
                    "company_id": r["company_id"],
                    "okved": f"{r['okved_code']} {r['okved_name']}",
                    "region": r["region"],
                    "segment": r["segment"],
                    "revenue_rub": float(r["revenue_rub"]) if r["revenue_rub"] is not None else None,
                    "products": list(r["products"]),
                    "fts_countries": list(r["fts_countries"]),
                    "rate_sensitive": r["rate_sensitive"],
                    "fx_sensitive": r["fx_sensitive"],
                    "group_size": r["group_size"],
                    "n_counterparties": r["n_cp"],
                    "completeness": r["completeness_score"],
                }
            )
            picked += 1
            if picked >= per_family:
                break
    return chosen
