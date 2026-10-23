"""Digest, UI events for the funnel metrics and the user's requests (CJM 01, 07, 08)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401, ERR_403
from app.api.v1.schemas import InsightCard
from app.domain.contracts.insight import Role
from app.services import feed as feed_svc
from app.services import interactions

router = APIRouter(tags=["engagement"])

# Events only the UI knows about; everything else is logged by the API itself (docs/METRICS.md).
UiEvent = Literal["widget_shown", "widget_click", "card_impression", "profile_prompt_shown", "digest_opened"]


class DigestCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new: int
    unread: int
    urgent: int
    risks: int
    opportunities: int
    updated: int
    hidden: int
    with_bank_analytics: int


class DigestNextStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    insight_id: uuid.UUID
    insight_title: str
    type: str
    text: str


class DigestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    role: Role
    period: Literal["day", "week"]
    since: datetime
    until: datetime
    counts: DigestCounts
    top: list[InsightCard]
    risks: list[InsightCard]
    opportunities: list[InsightCard]
    next_steps: list[DigestNextStep] = Field(
        description="Следующие шаги из самих инсайтов (по одному на инсайт)"
    )


@router.get(
    "/digest",
    response_model=DigestOut,
    responses={**ERR_401, **ERR_403},
    summary="Дайджест за день или неделю: что нового, главное, риски и возможности, следующие шаги",
)
async def get_digest(
    user: User,
    conn: Conn,
    settings: SettingsDep,
    company_id: Annotated[str, Query()],
    period: Annotated[Literal["day", "week"], Query()] = "day",
    role: Annotated[Role | None, Query()] = None,
) -> Any:
    access = await company_access(conn, user, company_id, settings)
    effective_role = role.value if role else access.role
    data = await feed_svc.digest(conn, user.id, company_id, effective_role, period)
    await interactions.log_action(conn, user.id, company_id, None, "digest_view", {"period": period})
    return data


class UiEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: UiEvent
    company_id: str
    insight_id: uuid.UUID | None = None
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=10)


@router.post(
    "/events",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**ERR_401, **ERR_403},
    summary="События интерфейса для воронки CJM (показ виджета, клик по нему, показ карточки)",
)
async def ui_event(body: UiEventIn, user: User, conn: Conn, settings: SettingsDep) -> None:
    await company_access(conn, user, body.company_id, settings)
    await interactions.log_action(conn, user.id, body.company_id, body.insight_id, body.type, body.payload)


class RequestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    external_ref: str
    company_id: str
    insight_id: uuid.UUID | None
    insight_title: str | None
    request_type: str
    product_id: str | None
    status: Literal["new", "in_progress", "done", "rejected"]
    created_at: datetime
    updated_at: datetime | None


@router.get(
    "/me/requests",
    response_model=list[RequestOut],
    responses=ERR_401,
    summary="Мои заявки после инсайтов и их статус (результат выбранного действия, CJM 07)",
)
async def my_requests(user: User, conn: Conn) -> Any:
    rows = await conn.fetch(
        """SELECT r.*, i.title AS insight_title FROM core.crm_requests r
           LEFT JOIN core.insights i ON i.id = r.insight_id
           WHERE r.user_id = $1 ORDER BY r.created_at DESC LIMIT 100""",
        user.id,
    )
    return [
        RequestOut(
            request_id=r["id"],
            external_ref=r["external_ref"],
            company_id=r["company_id"],
            insight_id=r["insight_id"],
            insight_title=r["insight_title"],
            request_type=r["request_type"],
            product_id=r["product_id"],
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]
