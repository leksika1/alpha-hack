"""In-app notifications (CJM stage 08 "оценить и вернуться"; SPEC FR-D): urgent or high-impact insights,
day/week digests and status changes of the user's requests. Delivered as a list and as an SSE stream.

Every notification has a per-user `dedup_key`, so regenerating an insight (another role variant, a new
context version) or re-running the digest never notifies twice about the same thing.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.services.users import company_title

log = get_logger(__name__)

NOTIFY_IMPACT_LEVELS = ("high", "critical")
DIGEST_PERIODS = {"day": timedelta(days=1), "week": timedelta(days=7)}
REQUEST_STATUS_TEXT = {
    "new": "принята",
    "in_progress": "в работе у менеджера",
    "done": "выполнена",
    "rejected": "отклонена",
}


async def _insert(
    conn: Any,
    user_ids: list[uuid.UUID],
    kind: str,
    dedup_key: str,
    title: str,
    body: str | None,
    company_id: str | None,
    insight_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> int:
    if not user_ids:
        return 0
    rows = await conn.fetch(
        """INSERT INTO core.notifications
               (user_id, company_id, insight_id, kind, title, body, payload, dedup_key)
           SELECT u, $2, $3, $4, $5, $6, $7::jsonb, $8 FROM unnest($1::uuid[]) AS u
           ON CONFLICT (user_id, dedup_key) DO NOTHING
           RETURNING id""",
        user_ids,
        company_id,
        insight_id,
        kind,
        title,
        body,
        json.dumps(payload, ensure_ascii=False, default=str),
        dedup_key,
    )
    return len(rows)


async def notify_insight(conn: Any, insight_id: uuid.UUID | str) -> int:
    """Urgent or high-impact insight -> users of the company who have not hidden the event."""
    row = await conn.fetchrow(
        """SELECT id, company_id, event_id, title, summary, urgent, impact_level, polarity
           FROM core.insights WHERE id = $1::uuid""",
        str(insight_id),
    )
    if row is None or not (row["urgent"] or row["impact_level"] in NOTIFY_IMPACT_LEVELS):
        return 0
    users = [
        r["user_id"]
        for r in await conn.fetch(
            """SELECT a.user_id FROM core.user_company_access a
               WHERE a.company_id = $1 AND NOT EXISTS (
                   SELECT 1 FROM core.user_insight_state s
                   WHERE s.user_id = a.user_id AND s.company_id = a.company_id AND s.event_id = $2
                     AND s.hidden_at IS NOT NULL)""",
            row["company_id"],
            row["event_id"],
        )
    ]
    prefix = "Срочно" if row["urgent"] else "Важно"
    return await _insert(
        conn,
        users,
        "urgent_insight",
        f"event:{row['event_id']}",
        f"{prefix}: {row['title']}",
        row["summary"],
        row["company_id"],
        row["id"],
        {"event_id": row["event_id"], "impact_level": row["impact_level"], "polarity": row["polarity"]},
    )


async def create_digests(conn: Any, period: str, now: datetime | None = None) -> int:
    """One digest notification per user x company with new unopened insights in the period."""
    now = now or datetime.now(UTC)
    since = now - DIGEST_PERIODS[period]
    rows = await conn.fetch(
        """SELECT a.user_id, a.company_id, c.okved_name, c.region,
                  count(DISTINCT i.event_id) AS new_count,
                  count(DISTINCT i.event_id) FILTER (WHERE i.urgent) AS urgent_count
           FROM core.user_company_access a
           JOIN core.companies c ON c.id = a.company_id
           JOIN core.insights i ON i.company_id = a.company_id AND i.created_at >= $1
           LEFT JOIN core.user_insight_state s
             ON s.user_id = a.user_id AND s.company_id = a.company_id AND s.event_id = i.event_id
           WHERE s.opened_at IS NULL AND s.hidden_at IS NULL
           GROUP BY a.user_id, a.company_id, c.okved_name, c.region""",
        since,
    )
    label = "день" if period == "day" else "неделю"
    created = 0
    for r in rows:
        created += await _insert(
            conn,
            [r["user_id"]],
            "digest",
            f"digest:{period}:{r['company_id']}:{now.date().isoformat()}",
            f"Дайджест за {label}: {r['new_count']} новых инсайтов, {r['urgent_count']} срочных",
            company_title(r["okved_name"], r["region"]),
            r["company_id"],
            None,
            {"period": period, "new": r["new_count"], "urgent": r["urgent_count"]},
        )
    return created


async def notify_request_update(conn: Any, request: Any) -> int:
    status = request["status"]
    return await _insert(
        conn,
        [request["user_id"]],
        "request_update",
        f"request:{request['id']}:{status}",
        f"Заявка {request['external_ref']} {REQUEST_STATUS_TEXT.get(status, status)}",
        None,
        request["company_id"],
        request["insight_id"],
        {"request_id": request["id"], "status": status, "external_ref": request["external_ref"]},
    )


def _out(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "body": r["body"],
        "company_id": r["company_id"],
        "insight_id": r["insight_id"],
        "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
        "created_at": r["created_at"],
        "read": r["read_at"] is not None,
    }


async def list_for_user(
    conn: Any, user_id: uuid.UUID, unread_only: bool, limit: int, before_id: int | None
) -> tuple[list[dict[str, Any]], int]:
    rows = await conn.fetch(
        """SELECT * FROM core.notifications
           WHERE user_id = $1 AND (NOT $2 OR read_at IS NULL) AND ($3::bigint IS NULL OR id < $3)
           ORDER BY id DESC LIMIT $4""",
        user_id,
        unread_only,
        before_id,
        limit,
    )
    unread: int = await conn.fetchval(
        "SELECT count(*) FROM core.notifications WHERE user_id = $1 AND read_at IS NULL", user_id
    )
    return [_out(r) for r in rows], unread


async def since(conn: Any, user_id: uuid.UUID, last_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT * FROM core.notifications WHERE user_id = $1 AND id > $2 ORDER BY id LIMIT $3",
        user_id,
        last_id,
        limit,
    )
    return [_out(r) for r in rows]


async def last_id(conn: Any, user_id: uuid.UUID) -> int:
    value: int = await conn.fetchval(
        "SELECT coalesce(max(id), 0) FROM core.notifications WHERE user_id = $1", user_id
    )
    return value


async def mark_read(conn: Any, user_id: uuid.UUID, notification_id: int | None) -> int:
    """One notification (id) or all of them (None). Returns the number marked."""
    result = await conn.execute(
        """UPDATE core.notifications SET read_at = now()
           WHERE user_id = $1 AND read_at IS NULL AND ($2::bigint IS NULL OR id = $2)""",
        user_id,
        notification_id,
    )
    return int(str(result).split()[-1])
