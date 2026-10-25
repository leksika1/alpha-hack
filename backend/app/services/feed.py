"""Personalized feed, widget summary, insight detail and trace.

One card per event: the variant for the requested role if it exists, otherwise the freshest other variant
(``role_match=false``) while the role-specific one is generated. Ordered by relevance, cursor-paginated.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

PERIODS = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}

# Macro themes that flood a feed with near-identical cards (the rate, the rouble): in the "all" view at most
# $9 cards per theme, the most relevant ones; each shown card says how many more there are (ADR-042).
# $9 = 0 — no cap (summary, digest, other filters show everything).
_FEED_SQL = """
WITH variants AS (
    SELECT DISTINCT ON (i.event_id) i.*, e.event_type, e.first_seen_at, (i.role_variant = $3) AS role_match,
           CASE WHEN e.event_type IN ('monetary_policy', 'fx') THEN e.event_type ELSE e.id::text END AS topic
    FROM core.insights i JOIN core.events e ON e.id = i.event_id
    WHERE i.company_id = $2 AND (i.expires_at IS NULL OR i.expires_at > now())
      AND ($4::timestamptz IS NULL OR e.first_seen_at >= $4)
    ORDER BY i.event_id, (i.role_variant = $3) DESC, i.created_at DESC
), visible AS (
    SELECT v.*, s.seen_at, s.opened_at, s.saved_at, s.hidden_at, s.last_insight_id
    FROM variants v
    LEFT JOIN core.user_insight_state s ON s.user_id = $1 AND s.company_id = v.company_id AND
        s.event_id = v.event_id
    WHERE ($5 = 'hidden') = (s.hidden_at IS NOT NULL)
      AND ($5 <> 'urgent' OR v.urgent)
      AND ($5 <> 'risks' OR v.polarity IN ('risk', 'mixed'))
      AND ($5 <> 'opportunities' OR v.polarity IN ('opportunity', 'mixed'))
      AND ($5 <> 'saved' OR s.saved_at IS NOT NULL)
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY topic ORDER BY relevance_score DESC, id DESC) AS topic_rank,
           count(*) OVER (PARTITION BY topic) AS topic_size
    FROM visible
)
SELECT *, CASE WHEN $9 > 0 AND $5 = 'all' THEN greatest(topic_size - $9, 0) ELSE 0 END AS more_in_topic
FROM ranked
WHERE ($9 = 0 OR $5 <> 'all' OR topic_rank <= $9)
  AND ($6::float8 IS NULL OR (relevance_score, id) < ($6, $7::uuid))
ORDER BY relevance_score DESC, id DESC
LIMIT $8
"""


def encode_cursor(score: float, insight_id: uuid.UUID) -> str:
    raw = json.dumps({"s": score, "id": str(insight_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[float, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    data = json.loads(raw)
    return float(data["s"]), uuid.UUID(data["id"])


# Readable labels of scoring components for "why this is higher in your feed" (CJM stage 03).
PRIORITY_LABELS = {
    "group": "событие касается вашей группы компаний",
    "counterparty": "затрагивает ваших контрагентов",
    "industry": "ваша отрасль",
    "fx_country": "страны ВЭД и валюта ваших расчётов",
    "rate": "ставка и ваши кредитные продукты",
    "commodity": "сырьё и материалы, от которых вы зависите",
    "user_provided": "данные, которые вы указали в профиле",
    "demand": "потребительский спрос",
    "regulation": "регулирование вашей деятельности",
    "region": "ваш регион",
    "bank_analytics": "есть аналитика Альфа-Банка по теме",
}
MAX_PRIORITY_REASONS = 2


def priority_reasons(breakdown: dict[str, Any]) -> list[str]:
    parts = sorted(
        (
            (k, float(v))
            for k, v in breakdown.items()
            if k != "recency" and isinstance(v, int | float) and v > 0
        ),
        key=lambda kv: -kv[1],
    )
    return [PRIORITY_LABELS.get(k, k) for k, _ in parts[:MAX_PRIORITY_REASONS]]


def card(r: Any) -> dict[str, Any]:
    payload = json.loads(r["payload"])
    reasons = payload.get("why_it_matters") or [{}]
    last_seen = r.get("last_insight_id")
    return {
        "insight_id": r["id"],
        "event_id": r["event_id"],
        "company_id": r["company_id"],
        "title": r["title"],
        "summary": r["summary"],
        "polarity": r["polarity"],
        "urgent": r["urgent"],
        "impact_level": r["impact_level"],
        "impact_score": r["impact_score"],
        "relevance_score": round(r["relevance_score"], 4),
        "role_variant": r["role_variant"],
        "role_match": bool(r["role_match"]),
        "event_type": r["event_type"],
        "published_at": r["first_seen_at"],
        "sources_count": len(payload.get("sources", [])),
        "bank_view_coverage": payload.get("bank_view", {}).get("coverage", "none"),
        "why_for_you": reasons[0].get("text"),
        "more_in_topic": int(r["more_in_topic"]),
        "priority_reasons": priority_reasons((payload.get("relevance") or {}).get("breakdown") or {}),
        "state": {
            "seen": r["seen_at"] is not None,
            "opened": r["opened_at"] is not None,
            "saved": r["saved_at"] is not None,
            "hidden": r["hidden_at"] is not None,
            # regenerated after the user saw it (event got new news, profile changed): "обновлено"
            "updated": r["seen_at"] is not None and last_seen is not None and last_seen != r["id"],
        },
    }


async def feed(
    conn: Any,
    user_id: uuid.UUID,
    company_id: str,
    role: str,
    filter_: str,
    period: str | None,
    limit: int,
    cursor: str | None,
    mark_seen: bool = True,
    topic_cap: int = 0,
) -> tuple[list[dict[str, Any]], str | None]:
    since = datetime.now(UTC) - PERIODS[period] if period in PERIODS else None
    cur_score, cur_id = decode_cursor(cursor) if cursor else (None, None)
    rows = await conn.fetch(
        _FEED_SQL, user_id, company_id, role, since, filter_, cur_score, cur_id, limit + 1, topic_cap
    )
    items = [card(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor(rows[limit - 1]["relevance_score"], rows[limit - 1]["id"])
        if len(rows) > limit
        else None
    )
    if mark_seen and items:
        await conn.execute(
            """INSERT INTO core.user_insight_state (user_id, company_id, event_id, last_insight_id, seen_at)
               SELECT $1, $2, x.event_id, x.insight_id, now()
               FROM unnest($3::uuid[], $4::uuid[]) AS x(event_id, insight_id)
               ON CONFLICT (user_id, company_id, event_id) DO UPDATE SET
                   seen_at = coalesce(core.user_insight_state.seen_at, now()),
                   last_insight_id = EXCLUDED.last_insight_id, updated_at = now()""",
            user_id,
            company_id,
            [i["event_id"] for i in items],
            [i["insight_id"] for i in items],
        )
    return items, next_cursor


async def company_summary(conn: Any, user_id: uuid.UUID, company_id: str, role: str) -> dict[str, Any]:
    rows = await conn.fetch(_FEED_SQL, user_id, company_id, role, None, "all", None, None, 500, 0)
    cards = [card(r) for r in rows]
    unread = [c for c in cards if not c["state"]["opened"]]
    urgent = [c for c in unread if c["urgent"]]
    pool = urgent or unread or cards
    top = pool[0] if pool else None
    return {"unread": len(unread), "urgent": len(urgent), "top": top}


DIGEST_TOP = 5


async def digest(conn: Any, user_id: uuid.UUID, company_id: str, role: str, period: str) -> dict[str, Any]:
    """Day/week digest (CJM 08): what is new for the company, the most relevant insights, risks and
    opportunities, and the next steps the insights themselves propose. No generated text."""
    now = datetime.now(UTC)
    since = now - PERIODS[period]
    rows = await conn.fetch(_FEED_SQL, user_id, company_id, role, since, "all", None, None, 500, 0)
    hidden = await conn.fetchval(
        """SELECT count(*) FROM core.user_insight_state s JOIN core.events e ON e.id = s.event_id
           WHERE s.user_id = $1 AND s.company_id = $2 AND s.hidden_at IS NOT NULL
             AND e.first_seen_at >= $3""",
        user_id,
        company_id,
        since,
    )
    cards = [card(r) for r in rows]
    payloads = {r["id"]: json.loads(r["payload"]) for r in rows}
    next_steps = []
    for c in cards[:DIGEST_TOP]:
        for action in (payloads[c["insight_id"]].get("actions") or [])[:1]:
            next_steps.append({"insight_id": c["insight_id"], "insight_title": c["title"], **action})
    return {
        "company_id": company_id,
        "role": role,
        "period": period,
        "since": since,
        "until": now,
        "counts": {
            "new": len(cards),
            "unread": sum(1 for c in cards if not c["state"]["opened"]),
            "urgent": sum(1 for c in cards if c["urgent"]),
            "risks": sum(1 for c in cards if c["polarity"] in ("risk", "mixed")),
            "opportunities": sum(1 for c in cards if c["polarity"] in ("opportunity", "mixed")),
            "updated": sum(1 for c in cards if c["state"]["updated"]),
            "hidden": hidden,
            "with_bank_analytics": sum(1 for c in cards if c["bank_view_coverage"] != "none"),
        },
        "top": cards[:DIGEST_TOP],
        "risks": [c for c in cards if c["polarity"] in ("risk", "mixed")][:3],
        "opportunities": [c for c in cards if c["polarity"] in ("opportunity", "mixed")][:3],
        "next_steps": next_steps,
    }


async def insight_row(conn: Any, insight_id: uuid.UUID, user_id: uuid.UUID) -> Any | None:
    return await conn.fetchrow(
        """SELECT i.*, s.seen_at, s.opened_at, s.saved_at, s.hidden_at, a.role AS user_role
           FROM core.insights i
           LEFT JOIN core.user_insight_state s
             ON s.user_id = $2 AND s.company_id = i.company_id AND s.event_id = i.event_id
           LEFT JOIN core.user_company_access a ON a.user_id = $2 AND a.company_id = i.company_id
           WHERE i.id = $1""",
        insight_id,
        user_id,
    )


def state_of(r: Any) -> dict[str, bool]:
    return {
        "seen": r["seen_at"] is not None,
        "opened": r["opened_at"] is not None,
        "saved": r["saved_at"] is not None,
        "hidden": r["hidden_at"] is not None,
    }


def _collect_feature_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for reason in payload.get("why_it_matters", []):
        refs.extend(reason.get("feature_refs", []))
    for st in payload.get("statements", []):
        refs.extend(st.get("feature_refs", []))
    for q in payload.get("impact", {}).get("quantitative", []):
        refs.extend(q.get("feature_refs", []))
    refs.extend(payload.get("relevance", {}).get("matched_features", []))
    return list(dict.fromkeys(refs))


def _step(r: Any) -> dict[str, Any]:
    return {
        "seq": r["seq"],
        "step_type": r["step_type"],
        "name": r["name"],
        "status": r["status"],
        "input_preview": r["input_preview"],
        "output_preview": r["output_preview"],
        "data": json.loads(r["data"]) if r["data"] else None,
        "model": r["model"],
        "tokens_in": r["tokens_in"],
        "tokens_out": r["tokens_out"],
        "latency_ms": r["latency_ms"],
        "error": r["error"],
        "attempt": r["attempt"],
        "created_at": r["created_at"],
    }


async def trace(conn: Any, row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    run_id = row["trace_id"]
    run = await conn.fetchrow("SELECT * FROM core.agent_runs WHERE id = $1", run_id)
    steps = await conn.fetch("SELECT * FROM core.agent_steps WHERE run_id = $1 ORDER BY seq", run_id)
    calls = await conn.fetch(
        """SELECT tool, args, status_code, latency_ms, result_preview, created_at
           FROM core.tool_audit_log WHERE run_id = $1 ORDER BY id""",
        run_id,
    )
    llm_calls = await conn.fetch(
        """SELECT model, purpose, status, error, cache_hit, tokens_in, tokens_out, cost_rub, latency_ms,
                  prompt_preview, created_at
           FROM core.llm_usage WHERE run_id = $1 ORDER BY id""",
        run_id,
    )
    feats_raw = await conn.fetchval(
        "SELECT features FROM core.company_context WHERE company_id = $1", row["company_id"]
    )
    feats: dict[str, Any] = json.loads(feats_raw) if feats_raw else {}
    referenced: dict[str, Any] = {}
    for ref in _collect_feature_refs(payload):
        if ref in feats:
            referenced[ref] = feats[ref]
        else:
            referenced[ref] = {k: v for k, v in feats.items() if k.startswith(ref + ".")} or None
    candidate = await conn.fetchrow(
        """SELECT score, score_breakdown, matched_features, scorer, status FROM core.candidate_matches
           WHERE company_id = $1 AND event_id = $2 AND role = $3""",
        row["company_id"],
        row["event_id"],
        row["role_variant"],
    )
    kinds: dict[str, int] = {}
    for st in payload.get("statements", []):
        kinds[st["kind"]] = kinds.get(st["kind"], 0) + 1
    validation_step = next((s for s in steps if s["name"] == "backend.validate"), None)
    return {
        "insight_id": row["id"],
        "trace_id": run_id,
        "run": None
        if run is None
        else {
            "run_id": run["id"],
            "kind": run["kind"],
            "generator": run["generator"],
            "status": run["status"],
            "outcome": run["outcome"],
            "usage": json.loads(run["usage"]),
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        },
        "steps": [_step(s) for s in steps],
        "tool_calls": [
            {
                "tool": c["tool"],
                "args": json.loads(c["args"]),
                "status_code": c["status_code"],
                "latency_ms": c["latency_ms"],
                "result_preview": c["result_preview"],
                "created_at": c["created_at"],
            }
            for c in calls
        ],
        "llm_calls": [
            {
                "model": c["model"],
                "purpose": c["purpose"],
                "status": c["status"],
                "error": c["error"],
                "cache_hit": c["cache_hit"],
                "tokens_in": c["tokens_in"],
                "tokens_out": c["tokens_out"],
                "cost_rub": float(c["cost_rub"]),
                "latency_ms": c["latency_ms"],
                "prompt_preview": c["prompt_preview"],
                "created_at": c["created_at"],
            }
            for c in llm_calls
        ],
        "company_features": referenced,
        "candidate": None
        if candidate is None
        else {
            "score": candidate["score"],
            "breakdown": json.loads(candidate["score_breakdown"]),
            "matched_features": list(candidate["matched_features"]),
            "scorer": candidate["scorer"],
            "status": candidate["status"],
        },
        "sources": payload.get("sources", []),
        "statements_by_kind": kinds,
        "validation": {
            "status": "passed" if validation_step and validation_step["status"] == "ok" else "unknown",
            "rules": "contracts/insight.schema.json + contracts/README.md (бизнес-правила)",
        },
    }
