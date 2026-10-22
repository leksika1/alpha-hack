"""Company profile (what we know and from where), user-provided facts and the relationship graph."""

import json
from typing import Any

from app.services.users import company_title

SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "identity",
        "Кто компания",
        (
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
    ),
    ("scale", "Масштаб и группа", ("revenue_rub", "group.", "segment")),
    ("products", "Продукты Альфа-Банка", ("products.",)),
    ("sensitivities", "Чувствительность к внешним факторам", ("sensitivities.",)),
    ("foreign_trade", "Внешнеэкономическая деятельность", ("fts_countries",)),
    ("counterparties", "Контрагенты", ("counterparties.",)),
    ("links", "Связи", ("links.",)),
    ("user_provided", "Сведения от вас", ("user_provided.",)),
]

SOURCES_LEGEND = {
    "csv": "данные банка о компании",
    "derived": "вычислено сервисом из данных банка",
    "user": "указано пользователем",
    "external": "внешний источник (указывается отдельно)",
}
USER_FIELDS = ("suppliers", "customers", "raw_materials", "currencies", "credit_plans", "notes")


def _section_of(path: str) -> str:
    for key, _, prefixes in SECTIONS:
        if any(path == p or path.startswith(p) for p in prefixes):
            return key
    return "identity"


async def profile(conn: Any, company_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """SELECT ctx.*, c.okved_name, c.region AS c_region FROM core.company_context ctx
           JOIN core.companies c ON c.id = ctx.company_id WHERE ctx.company_id = $1""",
        company_id,
    )
    if row is None:
        return None
    features: dict[str, Any] = json.loads(row["features"])
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in SECTIONS}
    for path in sorted(features):
        f = features[path]
        grouped[_section_of(path)].append(
            {
                "path": path,
                "value": f.get("value"),
                "source": f.get("source", "derived"),
                "note": f.get("note"),
            }
        )
    return {
        "company_id": company_id,
        "title": company_title(row["okved_name"], row["c_region"]),
        "context_version": row["context_version"],
        "computed_at": row["computed_at"],
        "sections": [{"key": k, "title": t, "features": grouped[k]} for k, t, _ in SECTIONS if grouped[k]],
        "completeness": json.loads(row["completeness"]),
        "user_provided": json.loads(row["user_provided"]),
        "sources_legend": SOURCES_LEGEND,
    }


async def update_user_provided(conn: Any, company_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge the questionnaire into user_provided; None clears a field."""
    current: dict[str, Any] = json.loads(
        await conn.fetchval(
            "SELECT user_provided FROM core.company_context WHERE company_id = $1", company_id
        )
        or "{}"
    )
    for key, value in changes.items():
        if key not in USER_FIELDS:
            continue
        if value in (None, "", []):
            current.pop(key, None)
        else:
            current[key] = value
    await conn.execute(
        "UPDATE core.company_context SET user_provided = $2::jsonb WHERE company_id = $1",
        company_id,
        json.dumps(current, ensure_ascii=False),
    )
    return current


async def graph(conn: Any, company_id: str, limit: int) -> dict[str, Any]:
    group = await conn.fetchrow(
        """SELECT g.id, g.valid_size, g.revenue_rub, g.segment, g.industries FROM core.company_groups g
           JOIN core.companies c ON c.group_id = g.id WHERE c.id = $1""",
        company_id,
    )
    feats_raw = await conn.fetchval(
        "SELECT features FROM core.company_context WHERE company_id = $1", company_id
    )
    feats: dict[str, Any] = json.loads(feats_raw) if feats_raw else {}
    rows = await conn.fetch(
        """SELECT e.*, CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END AS other,
                  CASE WHEN e.edge_type = 'shared_person' OR e.label = 'affiliated_outside_group' THEN
                      'undirected'
                       WHEN e.src_id = $1 THEN 'out' ELSE 'in' END AS direction,
                  c.okved_name, c.region, (c.group_id = me.group_id) AS same_group,
                  row_number() OVER (PARTITION BY e.edge_type, e.label ORDER BY e.weight DESC, e.id) AS rn
           FROM core.company_edges e
           JOIN core.companies me ON me.id = $1
           JOIN core.companies c ON c.id = CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END
           WHERE e.src_id = $1 OR e.dst_id = $1""",
        company_id,
    )
    edges = []
    for r in sorted(
        (r for r in rows if r["rn"] <= limit), key=lambda r: (r["edge_type"], r["label"], r["rn"])
    ):
        share = r["share_src"] if r["direction"] == "out" else r["share_dst"]
        edges.append(
            {
                "edge_type": r["edge_type"],
                "label": r["label"] or r["edge_type"],
                "direction": r["direction"],
                "counterparty": {
                    "company_id": r["other"],
                    "known": True,
                    "okved_name": r["okved_name"],
                    "region": r["region"],
                    "same_group": bool(r["same_group"]),
                },
                "amount_rub": float(r["amount_rub"]) if r["amount_rub"] is not None else None,
                "share": round(float(share), 4) if share is not None else None,
                "is_hypothesis": r["is_hypothesis"],
                "confidence": r["confidence"],
                "evidence": json.loads(r["evidence"]),
            }
        )
    return {
        "company_id": company_id,
        "group": None
        if group is None
        else {
            "group_id": group["id"],
            "size": group["valid_size"],
            "revenue_rub": float(group["revenue_rub"]) if group["revenue_rub"] is not None else None,
            "segment": group["segment"],
            "industries": json.loads(group["industries"]),
        },
        "unknown_counterparties": {
            "share_in": (feats.get("counterparties.unknown_share_in") or {}).get("value"),
            "share_out": (feats.get("counterparties.unknown_share_out") or {}).get("value"),
        },
        "edges": edges,
    }
