"""A user sends a news link or text for their company (ADR-038)."""

import uuid
from typing import Literal, Self

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401
from app.core.errors import ProblemError
from app.services import news_submit as svc

router = APIRouter(prefix="/news", tags=["news"])


class NewsSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(max_length=32)
    url: str | None = Field(default=None, max_length=1000, description="Ссылка на новость")
    title: str | None = Field(default=None, min_length=5, max_length=300, description="Или заголовок и текст")
    text: str | None = Field(default=None, min_length=40, max_length=20_000)

    @model_validator(mode="after")
    def link_or_text(self) -> Self:
        if bool(self.url) == bool(self.title and self.text):
            raise ValueError("send either url, or title and text")
        return self


class NewsSubmitOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["processed", "duplicate", "unavailable", "blocked", "not_article"] = Field(
        description="processed — обработана; duplicate — уже известна; unavailable — ресурс недоступен для "
        "просмотра; blocked — нарушает законодательство РФ или из реестра; not_article — не статья"
    )
    message: str = Field(description="Что показать пользователю")
    reason: str | None = Field(description="Подробность: почему недоступна или заблокирована")
    news_id: uuid.UUID | None
    event_id: uuid.UUID | None
    title: str | None
    url: str | None
    relevant: bool = Field(description="Событие значимо для компании")
    generation_queued: bool = Field(description="Инсайт готовится — придёт в ленту и уведомлением")


@router.post(
    "/submit",
    response_model=NewsSubmitOut,
    responses={
        **ERR_401,
        403: {"description": "Нет доступа к компании"},
        422: {"description": "Нужна ссылка или заголовок с текстом; неверная ссылка"},
        429: {"description": "Не больше 30 новостей в сутки"},
    },
    summary="Прислать ссылку или текст новости: обработка тем же конвейером, инсайт — для своей компании",
)
async def submit(
    body: NewsSubmitIn, request: Request, user: User, conn: Conn, settings: SettingsDep
) -> NewsSubmitOut:
    await company_access(conn, user, body.company_id, settings)
    try:
        result = await svc.submit(
            conn,
            settings,
            user.id,
            body.company_id,
            url=body.url,
            title=body.title,
            text=body.text,
            enqueue=request.app.state.enqueue,
        )
    except svc.DailyLimitError as exc:
        raise ProblemError(429, "rate-limited", "Too many news today", f"limit {svc.DAILY_LIMIT}") from exc
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid link", str(exc)) from exc
    return NewsSubmitOut(**result.as_dict())
