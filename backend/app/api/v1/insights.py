"""Insight detail, trace, state, feedback, source click and CTA actions (mock CRM)."""

import json
import uuid
from typing import Any

from fastapi import APIRouter, status

from app.api.deps import CompanyAccess, Conn, CurrentUser, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401, ERR_403, Responses
from app.api.v1.schemas import (
    ActionIn,
    ActionOut,
    FeedbackIn,
    FeedbackOut,
    InsightOut,
    InsightState,
    SourceClickIn,
    SourceClickOut,
    StateIn,
    TraceOut,
)
from app.core.config import Settings
from app.core.errors import ProblemError
from app.services import feed as feed_svc
from app.services import interactions as svc

router = APIRouter(prefix="/insights", tags=["insights"])
ERRS: Responses = {**ERR_401, **ERR_403, 404: {"description": "Инсайт не найден (problem+json)"}}


async def _load(
    conn: Any, user: CurrentUser, insight_id: uuid.UUID, settings: Settings
) -> tuple[Any, CompanyAccess]:
    row = await feed_svc.insight_row(conn, insight_id, user.id)
    if row is None:
        raise ProblemError(404, "not-found", "Insight not found")
    access = await company_access(conn, user, row["company_id"], settings)
    return row, access


@router.get("/{insight_id}", response_model=InsightOut, responses=ERRS, summary="Полный инсайт по контракту")
async def get_insight(insight_id: uuid.UUID, user: User, conn: Conn, settings: SettingsDep) -> InsightOut:
    row, access = await _load(conn, user, insight_id, settings)
    payload = json.loads(row["payload"])
    payload["insight_id"] = str(row["id"])
    return InsightOut(
        insight_id=row["id"],
        company_id=row["company_id"],
        created_at=row["created_at"],
        role_match=row["role_variant"] == access.role,
        state=InsightState(**feed_svc.state_of(row)),
        insight=payload,
    )


@router.get(
    "/{insight_id}/trace",
    response_model=TraceOut,
    responses=ERRS,
    summary="Трассировка: признаки компании, источники, шаги агента, вызовы инструментов, скоринг",
)
async def get_trace(insight_id: uuid.UUID, user: User, conn: Conn, settings: SettingsDep) -> TraceOut:
    row, _ = await _load(conn, user, insight_id, settings)
    return TraceOut(**await feed_svc.trace(conn, row))


@router.post(
    "/{insight_id}/state",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRS,
    summary="Открыть / скрыть / вернуть / сохранить инсайт",
)
async def set_state(
    insight_id: uuid.UUID, body: StateIn, user: User, conn: Conn, settings: SettingsDep
) -> None:
    row, _ = await _load(conn, user, insight_id, settings)
    await svc.set_state(conn, user.id, row, body.action)


@router.post(
    "/{insight_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRS,
    summary="Обратная связь: полезно / бесполезно и причина",
)
async def feedback(
    insight_id: uuid.UUID, body: FeedbackIn, user: User, conn: Conn, settings: SettingsDep
) -> FeedbackOut:
    row, access = await _load(conn, user, insight_id, settings)
    fid, at = await svc.add_feedback(conn, user.id, row, access.role, body.useful, body.reason, body.comment)
    return FeedbackOut(feedback_id=fid, recorded_at=at)


@router.post(
    "/{insight_id}/source-click",
    response_model=SourceClickOut,
    responses=ERRS,
    summary="Переход к первоисточнику (фиксируется для метрик)",
)
async def source_click(
    insight_id: uuid.UUID, body: SourceClickIn, user: User, conn: Conn, settings: SettingsDep
) -> SourceClickOut:
    row, _ = await _load(conn, user, insight_id, settings)
    src = await svc.resolve_source(conn, row, body.source_id)
    if src is None:
        raise ProblemError(404, "not-found", "Source not found in this insight", body.source_id)
    await svc.log_action(
        conn,
        user.id,
        row["company_id"],
        row["id"],
        "source_click",
        {"source_id": body.source_id, "type": src["type"]},
    )
    return SourceClickOut(**src)


@router.post(
    "/{insight_id}/actions",
    response_model=ActionOut,
    status_code=status.HTTP_201_CREATED,
    responses={**ERRS, 422: {"description": "Неизвестный продукт"}},
    summary="Действие после инсайта: связаться с менеджером, обратный звонок, заявка (мок CRM)",
)
async def create_action(
    insight_id: uuid.UUID, body: ActionIn, user: User, conn: Conn, settings: SettingsDep
) -> ActionOut:
    row, _ = await _load(conn, user, insight_id, settings)
    if body.product_id and not await conn.fetchval(
        "SELECT 1 FROM core.bank_products WHERE id = $1", body.product_id
    ):
        raise ProblemError(422, "validation", "Unknown product", body.product_id)
    result = await svc.create_crm_request(
        conn,
        user.id,
        row["company_id"],
        row["id"],
        body.type,
        body.product_id,
        body.comment,
        body.preferred_time,
    )
    await svc.log_action(
        conn,
        user.id,
        row["company_id"],
        row["id"],
        "cta",
        {"type": body.type, "product_id": body.product_id, "crm": result["external_ref"]},
    )
    return ActionOut(**result)
