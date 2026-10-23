"""Questions to the analytics (CJM 06): from an insight card or about the company in general. SSE answers."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401, ERR_403
from app.core.config import Settings
from app.core.errors import ProblemError
from app.integrations.generators import get_generator
from app.integrations.ml_client import HttpInsightGenerator
from app.services import feed as feed_svc
from app.services import qa as svc

router = APIRouter(tags=["questions"])

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=1000)]
SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERR_401,
    **ERR_403,
    200: {
        "content": {"text/event-stream": {}},
        "description": "SSE: `run` → `step`/`token`/`source` (от агента) → `done` (ChatFinal после проверки) "
        "или `error` (problem+json: ml-unavailable, agent-timeout, invalid-answer)",
    },
}


class InsightQuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    intent: Literal["clarify", "other_slice", "source", "free"] = Field(
        default="clarify",
        description="clarify — уточнить; other_slice — другой разрез (период, юрлицо группы); "
        "source — первоисточник",
    )


class CompanyQuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    question: Question
    intent: Literal["clarify", "other_slice", "source", "free"] = "free"


class QaMessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    role: Literal["user", "assistant"]
    content: str
    intent: str | None
    status: Literal["ok", "error"]
    run_id: str | None
    sources: list[dict[str, Any]]
    statements: list[dict[str, Any]]
    created_at: datetime


def chat_client(request: Request, settings: Settings) -> HttpInsightGenerator:
    """Tests route the ML call in-process through app.state.ml_transport."""
    return get_generator(settings, transport=getattr(request.app.state, "ml_transport", None))


def _stream(gen: Any) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post(
    "/insights/{insight_id}/ask",
    responses={**SSE_RESPONSES, 404: {"description": "Инсайт не найден"}},
    response_class=StreamingResponse,
    summary="Вопрос к инсайту: агент отвечает с источниками и ограничениями анализа (SSE)",
)
async def ask_insight(
    insight_id: uuid.UUID,
    body: InsightQuestionIn,
    request: Request,
    user: User,
    conn: Conn,
    settings: SettingsDep,
) -> StreamingResponse:
    row = await feed_svc.insight_row(conn, insight_id, user.id)
    if row is None:
        raise ProblemError(404, "not-found", "Insight not found")
    access = await company_access(conn, user, row["company_id"], settings)
    return _stream(
        svc.ask_stream(
            request.app.state.pool,
            settings,
            chat_client(request, settings),
            user.id,
            row["company_id"],
            access.role,
            row,
            body.question,
            body.intent,
        )
    )


@router.post(
    "/ask",
    responses=SSE_RESPONSES,
    response_class=StreamingResponse,
    summary="Вопрос к аналитике по компании (без привязки к инсайту), SSE",
)
async def ask_company(
    body: CompanyQuestionIn, request: Request, user: User, conn: Conn, settings: SettingsDep
) -> StreamingResponse:
    access = await company_access(conn, user, body.company_id, settings)
    return _stream(
        svc.ask_stream(
            request.app.state.pool,
            settings,
            chat_client(request, settings),
            user.id,
            body.company_id,
            access.role,
            None,
            body.question,
            body.intent,
        )
    )


@router.get(
    "/insights/{insight_id}/questions",
    response_model=list[QaMessageOut],
    responses={**ERR_401, **ERR_403, 404: {"description": "Инсайт не найден"}},
    summary="История вопросов и ответов по инсайту",
)
async def insight_history(insight_id: uuid.UUID, user: User, conn: Conn, settings: SettingsDep) -> Any:
    row = await feed_svc.insight_row(conn, insight_id, user.id)
    if row is None:
        raise ProblemError(404, "not-found", "Insight not found")
    await company_access(conn, user, row["company_id"], settings)
    return await svc.history(conn, user.id, row["company_id"], row["id"])


@router.get(
    "/ask/history",
    response_model=list[QaMessageOut],
    responses={**ERR_401, **ERR_403},
    summary="История вопросов по компании (без привязки к инсайту)",
)
async def company_history(
    user: User, conn: Conn, settings: SettingsDep, company_id: Annotated[str, Query()]
) -> Any:
    await company_access(conn, user, company_id, settings)
    return await svc.history(conn, user.id, company_id, None)
