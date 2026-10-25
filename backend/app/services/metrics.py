"""Quality and product metrics along the CJM (docs/METRICS.md): north star, the metric of every CJM stage and
generation quality. Computed from core.user_actions (sessions: a gap of 30+ minutes starts a new one),
feedback, requests and insights; daily snapshots go to core.metrics_daily.
"""

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

SESSION_GAP = timedelta(minutes=30)
TOP_K = 5
RETURN_WINDOW = timedelta(days=7)

_SESSIONS_SQL = """
WITH ev AS (
    SELECT user_id, action_type, insight_id, payload, created_at,
           CASE WHEN lag(created_at) OVER w IS NULL OR created_at - lag(created_at) OVER w > $3::interval
                THEN 1 ELSE 0 END AS new_session
    FROM core.user_actions
    WHERE created_at >= $1 AND created_at < $2
    WINDOW w AS (PARTITION BY user_id ORDER BY created_at)
), s AS (
    SELECT *, sum(new_session) OVER (PARTITION BY user_id ORDER BY created_at) AS session_no FROM ev
)
SELECT user_id, session_no, min(created_at) AS started_at, max(created_at) AS ended_at,
       array_agg(DISTINCT action_type) AS actions,
       bool_or(action_type = 'feedback' AND (payload ->> 'useful')::boolean) AS useful,
       bool_or(action_type IN ('cta', 'watch')) AS next_step,
       bool_or(action_type IN ('summary_view', 'widget_shown')) AS widget_shown,
       bool_or(action_type IN ('widget_click', 'feed_view', 'insight_open')) AS opened_analytics,
       bool_or(action_type = 'profile_update') AS profile_fixed,
       min(created_at) FILTER (WHERE action_type = 'insight_open') AS first_open_at
FROM s GROUP BY user_id, session_no
"""


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    return round(v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2, 1)


async def compute(conn: Any, since: datetime, until: datetime) -> dict[str, Any]:
    sessions = await conn.fetch(_SESSIONS_SQL, since, until, SESSION_GAP)
    n_sessions = len(sessions)
    users = {s["user_id"] for s in sessions}

    # speed: time from the session start to the first insight the user marked useful
    first_useful = await conn.fetch(
        """SELECT f.user_id, min(f.created_at) AS at FROM core.feedback f
           WHERE f.useful AND f.created_at >= $1 AND f.created_at < $2 GROUP BY f.user_id""",
        since,
        until,
    )
    starts: dict[Any, list[datetime]] = {}
    for s in sessions:
        starts.setdefault(s["user_id"], []).append(s["started_at"])
    ttfu = []
    for r in first_useful:
        before = [t for t in starts.get(r["user_id"], []) if t <= r["at"]]
        if before:
            ttfu.append((r["at"] - max(before)).total_seconds())

    # quality: usefulness of the top positions (feedback on insights opened at position <= TOP_K)
    top_k = await conn.fetchrow(
        """SELECT count(*) AS rated, count(*) FILTER (WHERE f.useful) AS useful FROM core.feedback f
           WHERE f.created_at >= $1 AND f.created_at < $2 AND EXISTS (
               SELECT 1 FROM core.user_actions a
               WHERE a.user_id = f.user_id AND a.insight_id = f.insight_id AND a.action_type = 'insight_open'
                 AND (a.payload ->> 'position')::int <= $3)""",
        since,
        until,
        TOP_K,
    )
    feedback = await conn.fetchrow(
        """SELECT count(*) AS total, count(*) FILTER (WHERE useful) AS useful
           FROM core.feedback WHERE created_at >= $1 AND created_at < $2""",
        since,
        until,
    )
    reasons = await conn.fetch(
        """SELECT coalesce(reason, 'none') AS reason, count(*) AS n FROM core.feedback
           WHERE NOT useful AND created_at >= $1 AND created_at < $2 GROUP BY 1 ORDER BY 2 DESC""",
        since,
        until,
    )
    statements = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE st ->> 'kind' IN ('fact', 'bank_analytics')) AS key_total,
                  count(*) FILTER (WHERE st ->> 'kind' IN ('fact', 'bank_analytics')
                                   AND jsonb_array_length(coalesce(st -> 'source_refs', '[]')) > 0)
                      AS key_sourced,
                  count(DISTINCT i.id) AS insights,
                  count(DISTINCT i.id) FILTER (WHERE i.payload::jsonb -> 'bank_view' ->> 'coverage' <> 'none')
                      AS with_bank_view
           FROM core.insights i, jsonb_array_elements(i.payload::jsonb -> 'statements') st
           WHERE i.created_at >= $1 AND i.created_at < $2""",
        since,
        until,
    )
    asks = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE role = 'user') AS asked,
                  count(*) FILTER (WHERE role = 'assistant' AND status = 'ok') AS answered
           FROM core.qa_messages WHERE created_at >= $1 AND created_at < $2""",
        since,
        until,
    )
    asks_resolved = await conn.fetchval(
        """SELECT count(DISTINCT q.id) FROM core.qa_messages q JOIN core.feedback f
             ON f.user_id = q.user_id AND f.insight_id = q.insight_id AND f.useful
            AND f.created_at BETWEEN q.created_at AND q.created_at + $3::interval
           WHERE q.role = 'user' AND q.created_at >= $1 AND q.created_at < $2""",
        since,
        until,
        SESSION_GAP,
    )
    requests = await conn.fetchrow(
        """SELECT count(*) AS total, count(*) FILTER (WHERE status = 'done') AS done
           FROM core.crm_requests WHERE created_at >= $1 AND created_at < $2""",
        since,
        until,
    )
    watches = await conn.fetchval(
        "SELECT count(*) FROM core.user_actions WHERE action_type = 'watch' "
        "AND created_at >= $1 AND created_at < $2",
        since,
        until,
    )
    hides = await conn.fetchval(
        "SELECT count(*) FROM core.user_actions WHERE action_type = 'hide' "
        "AND created_at >= $1 AND created_at < $2",
        since,
        until,
    )
    # return: users active in the window who came back 1..7 days after a session
    returned = await conn.fetchval(
        """SELECT count(DISTINCT a.user_id) FROM core.user_actions a
           WHERE a.created_at >= $1 AND a.created_at < $2 AND EXISTS (
               SELECT 1 FROM core.user_actions b WHERE b.user_id = a.user_id
                 AND b.created_at > a.created_at + interval '1 day'
                 AND b.created_at <= a.created_at + $3::interval)""",
        since,
        until,
        RETURN_WINDOW,
    )
    generation = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE outcome = 'completed') AS completed,
                  count(*) FILTER (WHERE outcome = 'rejected') AS rejected,
                  count(*) FILTER (WHERE outcome = 'skipped') AS skipped,
                  count(*) FILTER (WHERE status = 'error') AS failed
           FROM core.agent_runs WHERE kind = 'insight' AND started_at >= $1 AND started_at < $2""",
        since,
        until,
    )
    reject_reasons = await conn.fetch(
        """SELECT r ->> 'code' AS code, count(*) AS n
           FROM core.insights_rejected, jsonb_array_elements(reasons) r
           WHERE created_at >= $1 AND created_at < $2 GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
        since,
        until,
    )

    def sessions_with(flag: str) -> int:
        return sum(1 for s in sessions if s[flag])

    widget = [s for s in sessions if s["widget_shown"]]
    stages = {
        "01_notice": {
            "metric": "Доля открытий аналитики из показов блока на главной",
            "value": _share(sum(1 for s in widget if s["opened_analytics"]), len(widget)),
            "sessions_with_widget": len(widget),
        },
        "02_context": {
            "metric": "Доля сессий с исправлением профиля",
            "value": _share(sessions_with("profile_fixed"), n_sessions),
            "median_s_to_first_card": _median(
                [
                    (s["first_open_at"] - s["started_at"]).total_seconds()
                    for s in sessions
                    if s["first_open_at"]
                ]
            ),
        },
        "03_select": {
            "metric": f"Доля полезных среди оценённых инсайтов в топ-{TOP_K}",
            "value": _share(top_k["useful"], top_k["rated"]),
            "rated": top_k["rated"],
        },
        "04_understand": {
            "metric": "Доля верно объяснивших влияние — экспертная/опросная оценка, вне логов",
            "value": None,
        },
        "05_verify": {
            "metric": "Доля ключевых выводов (fact, bank_analytics) с источниками",
            "value": _share(statements["key_sourced"], statements["key_total"]),
            "insights_with_bank_view": _share(statements["with_bank_view"], statements["insights"]),
        },
        "06_clarify": {
            "metric": "Доля уточнений, после которых инсайт отмечен полезным",
            "value": _share(asks_resolved, asks["asked"]),
            "answered_and_passed_check": _share(asks["answered"], asks["asked"]),
            "questions": asks["asked"],
        },
        "07_act": {
            "metric": "Доля заявок с подтверждённым завершением; «наблюдать» отдельно",
            "value": _share(requests["done"], requests["total"]),
            "requests": requests["total"],
            "watch": watches,
        },
        "08_return": {
            "metric": "Доля пользователей, вернувшихся за 7 дней",
            "value": _share(returned, len(users)),
            "hidden": hides,
            "not_useful_reasons": {r["reason"]: r["n"] for r in reasons},
        },
    }
    north_star = sum(1 for s in sessions if s["useful"] and s["next_step"])
    return {
        "since": since,
        "until": until,
        "sessions": n_sessions,
        "users": len(users),
        "north_star": {
            "metric": "Доля сессий с полезным инсайтом и понятным следующим шагом",
            "value": _share(north_star, n_sessions),
            "sessions_with_useful_insight": sessions_with("useful"),
            "sessions_with_next_step": sessions_with("next_step"),
        },
        "speed": {"median_s_to_first_useful": _median(ttfu), "users_with_useful": len(ttfu)},
        "quality": {
            "useful_share": _share(feedback["useful"], feedback["total"]),
            "feedback": feedback["total"],
            "top_k_useful_share": stages["03_select"]["value"],
            "key_statements_sourced": stages["05_verify"]["value"],
        },
        "return": {"returned_7d_share": stages["08_return"]["value"], "hidden": hides},
        "cjm": stages,
        "generation": {
            "completed": generation["completed"],
            "rejected": generation["rejected"],
            "skipped": generation["skipped"],
            "failed": generation["failed"],
            "rejected_share": _share(
                generation["rejected"], generation["completed"] + generation["rejected"]
            ),
            "reject_reasons": {r["code"]: r["n"] for r in reject_reasons},
        },
    }


async def snapshot_day(conn: Any, day: date) -> dict[str, Any]:
    since = datetime.combine(day, time.min, tzinfo=UTC)
    data = await compute(conn, since, since + timedelta(days=1))
    await conn.execute(
        """INSERT INTO core.metrics_daily (day, metrics) VALUES ($1, $2::jsonb)
           ON CONFLICT (day) DO UPDATE SET metrics = EXCLUDED.metrics, computed_at = now()""",
        day,
        json.dumps(data, ensure_ascii=False, default=str),
    )
    return data


async def daily_series(conn: Any, since: date) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT day, metrics FROM core.metrics_daily WHERE day >= $1 ORDER BY day", since)
    out = []
    for r in rows:
        m = json.loads(r["metrics"])
        out.append(
            {
                "date": r["day"].isoformat(),
                "sessions": m["sessions"],
                "north_star": m["north_star"]["value"],
                "useful_share": m["quality"]["useful_share"],
                "returned_7d_share": m["return"]["returned_7d_share"],
            }
        )
    return out
