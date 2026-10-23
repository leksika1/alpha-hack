"""Notifications (CJM 08): list, read marks and an SSE stream for the widget and the bell icon."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import Conn, SettingsDep, User
from app.api.v1.auth import ERR_401
from app.core.errors import ProblemError
from app.services import interactions
from app.services import notifications as svc

router = APIRouter(prefix="/notifications", tags=["notifications"])

HEARTBEAT_S = 15.0


class NotificationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    kind: Literal["urgent_insight", "digest", "request_update"]
    title: str
    body: str | None
    company_id: str | None
    insight_id: str | None
    payload: dict[str, Any]
    created_at: datetime
    read: bool


class NotificationsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationOut]
    unread: int
    next_before_id: int | None


def _model(n: dict[str, Any]) -> NotificationOut:
    return NotificationOut(**{**n, "insight_id": str(n["insight_id"]) if n["insight_id"] else None})


@router.get(
    "",
    response_model=NotificationsOut,
    responses=ERR_401,
    summary="Уведомления пользователя (новые сверху); before_id — следующая страница",
)
async def list_notifications(
    user: User,
    conn: Conn,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> NotificationsOut:
    items, unread = await svc.list_for_user(conn, user.id, unread_only, limit, before_id)
    return NotificationsOut(
        items=[_model(n) for n in items],
        unread=unread,
        next_before_id=items[-1]["id"] if len(items) == limit else None,
    )


@router.post(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**ERR_401, 404: {"description": "Уведомление не найдено или уже прочитано"}},
    summary="Отметить уведомление прочитанным (фиксируется переход из уведомления)",
)
async def read_one(notification_id: int, user: User, conn: Conn) -> None:
    row = await conn.fetchrow(
        "SELECT company_id, insight_id, kind FROM core.notifications WHERE id = $1 AND user_id = $2",
        notification_id,
        user.id,
    )
    if row is None:
        raise ProblemError(404, "not-found", "Notification not found")
    await svc.mark_read(conn, user.id, notification_id)
    if row["company_id"]:
        await interactions.log_action(
            conn, user.id, row["company_id"], row["insight_id"], "notification_open", {"kind": row["kind"]}
        )


@router.post(
    "/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERR_401,
    summary="Прочитать все уведомления",
)
async def read_all(user: User, conn: Conn) -> None:
    await svc.mark_read(conn, user.id, None)


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get(
    "/stream",
    responses={**ERR_401, 200: {"content": {"text/event-stream": {}}, "description": "SSE-поток"}},
    summary="SSE: новые уведомления (`ready`, `notification`, комментарии-пинги). Соединение живёт до max_s "
    "секунд; клиент переподключается с заголовком Last-Event-ID и получает пропущенное",
)
async def stream(
    request: Request,
    user: User,
    settings: SettingsDep,
    max_s: Annotated[int | None, Query(ge=1, le=3600)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    pool = request.app.state.pool
    duration = max_s or settings.notifications_stream_max_s

    async def events() -> AsyncIterator[str]:
        async with pool.acquire() as conn:
            last = (
                int(last_event_id)
                if last_event_id and last_event_id.isdigit()
                else await svc.last_id(conn, user.id)
            )
            _, unread = await svc.list_for_user(conn, user.id, True, 1, None)
        yield f"retry: {settings.notifications_retry_ms}\n\n"
        yield _sse("ready", {"unread": unread, "last_id": last})
        started = beat = time.monotonic()
        while time.monotonic() - started < duration:
            if await request.is_disconnected():
                return
            async with pool.acquire() as conn:
                items = await svc.since(conn, user.id, last)
            for n in items:
                last = n["id"]
                yield _sse("notification", _model(n).model_dump(mode="json"), n["id"])
            if time.monotonic() - beat >= HEARTBEAT_S:
                beat = time.monotonic()
                yield ": ping\n\n"
            await asyncio.sleep(
                min(settings.notifications_poll_s, max(0.0, duration - (time.monotonic() - started)))
            )

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
