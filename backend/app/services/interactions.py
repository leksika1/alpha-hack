"""User interactions with insights: state, feedback, source clicks and CTA requests to the mock CRM."""

import json
import secrets
import uuid
from datetime import datetime
from typing import Any

STATE_COLUMNS = {
    "open": ("opened_at", "now()"),
    "hide": ("hidden_at", "now()"),
    "unhide": ("hidden_at", "NULL"),
    "save": ("saved_at", "now()"),
    "unsave": ("saved_at", "NULL"),
}

CTA_MESSAGES = {
    "contact_manager": "Заявка передана персональному менеджеру; он свяжется с вами в рабочее время.",
    "callback": "Запрос на обратный звонок принят.",
    "application": "Заявка на продукт зарегистрирована; менеджер уточнит детали.",
}


async def set_state(conn: Any, user_id: uuid.UUID, insight: Any, action: str) -> None:
    column, value = STATE_COLUMNS[action]
    # column/value come from the fixed STATE_COLUMNS map, never from user input
    await conn.execute(
        f"""INSERT INTO core.user_insight_state (user_id, company_id, event_id, last_insight_id, {column})
            VALUES ($1, $2, $3, $4, {value})
            ON CONFLICT (user_id, company_id, event_id) DO UPDATE SET {column} = {value},
                last_insight_id = EXCLUDED.last_insight_id, updated_at = now()""",  # noqa: S608
        user_id,
        insight["company_id"],
        insight["event_id"],
        insight["id"],
    )


async def add_feedback(
    conn: Any,
    user_id: uuid.UUID,
    insight: Any,
    role: str,
    useful: bool,
    reason: str | None,
    comment: str | None,
) -> tuple[int, datetime]:
    row = await conn.fetchrow(
        """INSERT INTO core.feedback (user_id, insight_id, company_id, event_id, role, useful, reason,
               comment)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id, created_at""",
        user_id,
        insight["id"],
        insight["company_id"],
        insight["event_id"],
        role,
        useful,
        reason,
        comment,
    )
    return row["id"], row["created_at"]


async def log_action(
    conn: Any,
    user_id: uuid.UUID,
    company_id: str,
    insight_id: uuid.UUID | None,
    action: str,
    payload: dict[str, Any],
) -> None:
    await conn.execute(
        """INSERT INTO core.user_actions (user_id, company_id, insight_id, action_type, payload)
           VALUES ($1, $2, $3, $4, $5::jsonb)""",
        user_id,
        company_id,
        insight_id,
        action,
        json.dumps(payload, ensure_ascii=False, default=str),
    )


async def resolve_source(conn: Any, insight: Any, source_id: str) -> dict[str, Any] | None:
    payload = json.loads(insight["payload"])
    src = next((s for s in payload.get("sources", []) if s["id"] == source_id), None)
    if src is None:
        return None
    note = None
    url = src.get("url")
    if src["type"] == "bank_analytics":
        url = None
        note = (
            "Ссылка на страницу PDF аналитики формируется через /analytics/documents/{id}/download (фаза 4)."
        )
    elif src["type"] == "company_data":
        url = f"/api/v1/companies/{insight['company_id']}/profile"
    elif src["type"] == "news" and src.get("ref_id"):
        is_fixture = await conn.fetchval(
            """SELECT s.is_fixture FROM core.news_items n JOIN core.sources s ON s.id = n.source_id
               WHERE n.id = $1::uuid""",
            src["ref_id"],
        )
        if is_fixture:
            note = "Синтетическая демо-новость: полный текст доступен по /api/v1/news/{id}."
            url = f"/api/v1/news/{src['ref_id']}"
    return {
        "source_id": source_id,
        "type": src["type"],
        "url": url,
        "title": src["title"],
        "page": src.get("page"),
        "note": note,
    }


async def create_crm_request(
    conn: Any,
    user_id: uuid.UUID,
    company_id: str,
    insight_id: uuid.UUID | None,
    request_type: str,
    product_id: str | None,
    comment: str | None,
    preferred_time: str | None,
) -> dict[str, Any]:
    external_ref = "CRM-" + secrets.token_hex(4).upper()
    row = await conn.fetchrow(
        """INSERT INTO core.crm_requests (user_id, company_id, insight_id, request_type, product_id, comment,
               preferred_time, external_ref)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id, status, created_at""",
        user_id,
        company_id,
        insight_id,
        request_type,
        product_id,
        comment,
        preferred_time,
        external_ref,
    )
    return {
        "request_id": row["id"],
        "external_ref": external_ref,
        "status": row["status"],
        "request_type": request_type,
        "product_id": product_id,
        "message": CTA_MESSAGES[request_type],
        "created_at": row["created_at"],
    }
