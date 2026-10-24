"""Admin management of news sources (ADR-036): which sites the news come from."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Admin, Conn, SettingsDep
from app.api.v1.admin import ADMIN_ERRS
from app.core.errors import ProblemError
from app.services import news_sources_admin as svc

router = APIRouter(prefix="/admin/sources", tags=["admin"])

SourceId = Annotated[str, Path(max_length=64)]


class SourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: str = Field(description="rss | fixture")
    url: str | None
    origin: Literal["catalog", "admin"] = Field(
        description="catalog — из news_sources.json (можно только включить или выключить); "
        "admin — добавлен админом"
    )
    enabled: bool = Field(description="Опрашивается сейчас")
    disabled_by_admin: bool
    is_fixture: bool
    priority: int
    fallback_for: str | None = Field(description="Резерв для источника: опрашивается, когда тот down")
    topics: list[str]
    health_status: str = Field(description="healthy | degraded | down | unknown")
    last_error: str | None
    last_checked_at: datetime | None
    last_success_at: datetime | None
    items_24h: int


class FeedCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=10, max_length=500, examples=["https://feeds.bbci.co.uk/news/business/rss.xml"]
    )


class FeedSampleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    published_at: datetime


class FeedCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    url: str
    outcome: str = Field(
        description="ok | empty (не лента) | http_error | network_error (в т.ч. непубличный адрес) | "
        "robots_disallowed | too_large"
    )
    status_code: int | None
    items: int
    sample: list[FeedSampleItem] = Field(description="До 5 последних новостей ленты")
    error: str | None


class SourceIn(FeedCheckIn):
    name: str = Field(min_length=2, max_length=120)
    topics: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=100, ge=1, le=1000)
    fallback_for: str | None = Field(default=None, max_length=64)


class SourceCreatedOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceOut
    check: FeedCheckOut
    task_id: str | None = Field(description="Опрос нового источника поставлен в очередь")


class SourcePatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=2, max_length=120)
    priority: int | None = Field(default=None, ge=1, le=1000)
    fallback_for: str | None = Field(default=None, max_length=64)
    topics: list[str] | None = Field(default=None, max_length=20)


def _invalid_url(exc: ValueError) -> ProblemError:
    return ProblemError(422, "validation", "Invalid source URL", str(exc))


@router.get("", response_model=list[SourceOut], responses=ADMIN_ERRS, summary="Все источники новостей")
async def list_sources(admin: Admin, conn: Conn) -> list[dict[str, Any]]:
    return await svc.list_sources(conn)


@router.post(
    "/check",
    response_model=FeedCheckOut,
    responses={**ADMIN_ERRS, 422: {"description": "Неверный URL"}},
    summary="Проверить ленту, ничего не сохраняя: доступность, robots.txt, публичный адрес, разбор "
    "RSS/Atom и последние новости",
)
async def check(body: FeedCheckIn, admin: Admin, settings: SettingsDep) -> dict[str, Any]:
    try:
        return (await svc.check_feed(settings, body.url)).as_dict()
    except ValueError as exc:
        raise _invalid_url(exc) from exc


@router.post(
    "",
    response_model=SourceCreatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **ADMIN_ERRS,
        409: {"description": "Такая лента уже есть"},
        422: {"description": "Неверный URL, лента не прошла проверку или неизвестный fallback_for"},
    },
    summary="Добавить источник (RSS/Atom): сначала проверка ленты, затем опрос в очереди",
)
async def create(
    body: SourceIn,
    admin: Admin,
    conn: Conn,
    settings: SettingsDep,
    poll_now: Annotated[bool, Query(description="Сразу поставить опрос в очередь")] = True,
) -> dict[str, Any]:
    from app.workers.tasks import poll_news_task

    try:
        result = await svc.check_feed(settings, body.url)
    except ValueError as exc:
        raise _invalid_url(exc) from exc
    if not result.ok:
        raise ProblemError(
            422,
            "feed-check-failed",
            "The feed did not pass the check",
            f"{result.outcome}: {result.error}",
            extra={"check": result.as_dict()},
        )
    try:
        source = await svc.create_source(
            conn,
            settings,
            name=body.name,
            url=result.url,
            topics=body.topics,
            priority=body.priority,
            fallback_for=body.fallback_for,
        )
    except svc.SourceConflictError as exc:
        raise ProblemError(409, "conflict", "This feed is already a source", str(exc)) from exc
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid source", str(exc)) from exc
    task_id = (await poll_news_task.kiq(source_ids=[source["id"]])).task_id if poll_now else None
    return {"source": source, "check": result.as_dict(), "task_id": task_id}


@router.patch(
    "/{source_id}",
    response_model=SourceOut,
    responses={
        **ADMIN_ERRS,
        404: {"description": "Источник не найден"},
        422: {"description": "Источник из каталога: можно только enabled; неизвестный fallback_for"},
    },
    summary="Включить или выключить источник; у добавленных админом — ещё название, приоритет, резерв, темы",
)
async def update(
    source_id: SourceId, body: SourcePatchIn, admin: Admin, conn: Conn, settings: SettingsDep
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    try:
        source = await svc.update_source(conn, settings, source_id, changes)
    except svc.CatalogSourceError as exc:
        raise ProblemError(
            422, "validation", "Catalog source", "only `enabled`; other fields come from news_sources.json"
        ) from exc
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid source", str(exc)) from exc
    if source is None:
        raise ProblemError(404, "not-found", "Source not found")
    return source


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **ADMIN_ERRS,
        404: {"description": "Источник не найден"},
        409: {"description": "Источник из каталога или по нему уже есть новости — выключите его"},
    },
    summary="Удалить источник, добавленный админом (пока по нему нет новостей)",
)
async def delete(source_id: SourceId, admin: Admin, conn: Conn) -> None:
    outcome = await svc.delete_source(conn, source_id)
    if outcome is None:
        raise ProblemError(404, "not-found", "Source not found")
    if outcome == "catalog":
        raise ProblemError(409, "conflict", "Catalog source", 'switch it off: PATCH {"enabled": false}')
    if outcome == "has-news":
        raise ProblemError(
            409, "conflict", "The source already has news", 'switch it off: PATCH {"enabled": false}'
        )
