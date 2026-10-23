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

_FEED_SQL = """
WITH variants AS (
    SELECT DISTINCT ON (i.event_id) i.*, e.event_type, e.first_seen_at, (i.role_variant = $3) AS role_match
    FROM core.insights i JOIN core.events e ON e.id = i.event_id
    WHERE i.company_id = $2 AND (i.expires_at IS NULL OR i.expires_at > now())
      AND ($4::timestamptz IS NULL OR e.first_seen_at >= $4)
    ORDER BY i.event_id, (i.role_variant = $3) DESC, i.created_at DESC
)
SELECT v.*, s.seen_at, s.opened_at, s.saved_at, s.hidden_at
FROM variants v
LEFT JOIN core.user_insight_state s ON s.user_id = $1 AND s.company_id = v.company_id AND
    s.event_id = v.event_id
WHERE ($5 = 'hidden') = (s.hidden_at IS NOT NULL)
  AND ($5 <> 'urgent' OR v.urgent)
  AND ($5 <> 'risks' OR v.polarity IN ('risk', 'mixed'))
  AND ($5 <> 'opportunities' OR v.polarity IN ('opportunity', 'mixed'))
  AND ($5 <> 'saved' OR s.saved_at IS NOT NULL)
  AND ($6::float8 IS NULL OR (v.relevance_score, v.id) < ($6, $7::uuid))
ORDER BY v.relevance_score DESC, v.id DESC
LIMIT $8
"""


def encode_cursor(score: float, insight_id: uuid.UUID) -> str:
    raw = json.dumps({"s": score, "id": str(insight_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[float, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    data = json.loads(raw)
    return float(data["s"]), uuid.UUID(data["id"])


def card(r: Any) -> dict[str, Any]:
    payload = json.loads(r["payload"])
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
        "state": {
            "seen": r["seen_at"] is not None,
            "opened": r["opened_at"] is not None,
            "saved": r["saved_at"] is not None,
            "hidden": r["hidden_at"] is not None,
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
) -> tuple[list[dict[str, Any]], str | None]:
    since = datetime.now(UTC) - PERIODS[period] if period in PERIODS else None
    cur_score, cur_id = decode_cursor(cursor) if cursor else (None, None)
    rows = await conn.fetch(
        _FEED_SQL, user_id, company_id, role, since, filter_, cur_score, cur_id, limit + 1
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
    rows = await conn.fetch(_FEED_SQL, user_id, company_id, role, None, "all", None, None, 500)
    cards = [card(r) for r in rows]
    unread = [c for c in cards if not c["state"]["opened"]]
    urgent = [c for c in unread if c["urgent"]]
    pool = urgent or unread or cards
    top = pool[0] if pool else None
    return {"unread": len(unread), "urgent": len(urgent), "top": top}


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
        """SELECT model, purpose, status, cache_hit, tokens_in, tokens_out, cost_rub, latency_ms,
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
