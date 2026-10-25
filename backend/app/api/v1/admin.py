"""Admin API (platform role `admin`, SPEC §10): quality metrics along the CJM, data quality, source health,
LLM spend, manual ingestion / regeneration and the mock CRM request statuses."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Admin, Conn, SettingsDep
from app.api.v1.auth import ERR_401
from app.core.errors import ProblemError
from app.services import llm_gateway, metrics
from app.services.notifications import notify_request_update

ADMIN_ERRS: dict[int | str, dict[str, Any]] = {**ERR_401, 403: {"description": "Нужна роль admin"}}

router = APIRouter(prefix="/admin", tags=["admin"])


class LlmUsageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    model: str
    purpose: str
    status: str
    error: str | None = Field(
        description="Код ошибки вызова: llm-not-configured (нет LLM_API_KEY), llm-upstream-error, "
        "llm-timeout, llm-unavailable, cancelled (агент не дождался), abandoned (резерв без результата) …"
    )
    calls: int
    tokens_in: int
    tokens_out: int
    cost_rub: float
    avg_latency_ms: float | None


class LlmBudgetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    limit_rub: float
    spent_rub: float
    remaining_rub: float
    tokens_in: int
    tokens_out: int
    exhausted: bool
    chat_limit_rub: float = Field(description="Предел вопросов к аналитике за день (LLM_CHAT_BUDGET_SHARE)")
    chat_spent_rub: float


class LlmUsageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: LlmBudgetOut
    rows: list[LlmUsageRow]


@router.get(
    "/llm-usage",
    response_model=LlmUsageOut,
    responses={**ERR_401, 403: {"description": "Нужна роль admin"}},
    summary="Расход LLM: бюджет дня и вызовы по дням, моделям, целям и статусам (ok, cached, refused, error)",
)
async def llm_usage(
    admin: Admin, conn: Conn, settings: SettingsDep, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> Any:
    return await llm_gateway.usage_summary(conn, settings, days)


@router.get(
    "/metrics",
    responses=ADMIN_ERRS,
    summary="Метрики качества по CJM: north star, этапы, скорость, качество, возврат, генерация",
)
async def get_metrics(
    admin: Admin, conn: Conn, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> dict[str, Any]:
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    data = await metrics.compute(conn, since, until)
    data["daily"] = await metrics.daily_series(conn, (until - timedelta(days=days)).date())
    return data


@router.get(
    "/data-quality", responses=ADMIN_ERRS, summary="Отчёт качества данных последней загрузки компаний"
)
async def data_quality(admin: Admin, conn: Conn) -> dict[str, Any]:
    run = await conn.fetchrow(
        "SELECT * FROM core.ingest_runs WHERE kind = 'companies' ORDER BY started_at DESC LIMIT 1"
    )
    if run is None:
        return {"run": None, "checks": []}
    checks = await conn.fetch(
        "SELECT dataset, check_name, severity, value, details FROM core.data_quality_report "
        "WHERE run_id = $1 "
        "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, dataset, check_name",
        run["id"],
    )
    return {
        "run": {
            "id": run["id"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "stats": json.loads(run["stats"]),
        },
        "checks": [
            {
                "dataset": c["dataset"],
                "check": c["check_name"],
                "severity": c["severity"],
                "value": c["value"],
                "details": json.loads(c["details"]),
            }
            for c in checks
        ],
    }


@router.get(
    "/sources/health", responses=ADMIN_ERRS, summary="Здоровье источников новостей и последние опросы"
)
async def sources_health(admin: Admin, conn: Conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT s.id, s.name, s.kind, s.enabled, s.is_fixture, s.priority, s.fallback_for, s.health_status,
                  s.consecutive_failures, s.last_checked_at, s.last_success_at, s.last_error,
                  s.last_latency_ms,
                  (SELECT count(*) FROM core.news_items n WHERE n.source_id = s.id
                     AND n.fetched_at > now() - interval '1 day') AS items_24h
           FROM core.sources s ORDER BY s.enabled DESC, s.priority, s.id"""
    )
    return [dict(r) for r in rows]


class IngestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    task_id: str
    status: Literal["queued"]


@router.post(
    "/ingest/{kind}",
    response_model=IngestOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **ADMIN_ERRS,
        422: {"description": "Неизвестный вид; компании загружаются CLI (make ingest-companies)"},
    },
    summary="Поставить в очередь сбор новостей или загрузку аналитики",
)
async def ingest(
    kind: str, admin: Admin, force: Annotated[bool, Query(description="analytics: переобработать")] = False
) -> IngestOut:
    from app.workers.tasks import ingest_analytics_task, poll_news_task

    if kind == "news":
        task: Any = await poll_news_task.kiq()
    elif kind == "analytics":
        task = await ingest_analytics_task.kiq(force=force)
    else:
        raise ProblemError(422, "validation", "Unknown ingest kind", "news | analytics (companies: CLI)")
    return IngestOut(kind=kind, task_id=task.task_id, status="queued")


class RegenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_ids: list[str] = Field(min_length=1, max_length=100)
    only_failed: bool = Field(default=False, description="Только отклонённые валидатором и упавшие")


class RegenerateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: int
    companies: list[str]


@router.post(
    "/insights/regenerate",
    response_model=RegenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ADMIN_ERRS,
    summary="Перегенерировать инсайты компаний (например, после подключения ML-сервиса)",
)
async def regenerate(body: RegenerateIn, request: Request, admin: Admin, conn: Conn) -> RegenerateOut:
    result = await conn.execute(
        """UPDATE core.candidate_matches SET status = 'pending', status_reason = 'admin', updated_at = now()
           WHERE company_id = ANY($1::text[]) AND (NOT $2 OR status IN ('rejected', 'failed'))""",
        body.company_ids,
        body.only_failed,
    )
    await request.app.state.enqueue(body.company_ids)
    return RegenerateOut(candidates=int(str(result).split()[-1]), companies=body.company_ids)


class CrmStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["new", "in_progress", "done", "rejected"]


@router.patch(
    "/crm-requests/{request_id}",
    responses={**ADMIN_ERRS, 404: {"description": "Заявка не найдена"}},
    summary="Мок CRM: сменить статус заявки (пользователь получит уведомление)",
)
async def set_crm_status(
    request_id: uuid.UUID, body: CrmStatusIn, admin: Admin, conn: Conn
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """UPDATE core.crm_requests SET status = $2, updated_at = now() WHERE id = $1
           RETURNING id, user_id, company_id, insight_id, status, external_ref, updated_at""",
        request_id,
        body.status,
    )
    if row is None:
        raise ProblemError(404, "not-found", "Request not found")
    notified = await notify_request_update(conn, row)
    return {
        "request_id": row["id"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "notified": notified,
    }


class ManualNewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=5, max_length=500)
    body: str = Field(min_length=20, max_length=20_000)
    url: str | None = Field(default=None, max_length=2000)
    published_at: datetime | None = None
    publisher: str | None = Field(default=None, max_length=200)


class ManualNewsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ManualNewsItem] = Field(min_length=1, max_length=20)


class ManualNewsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    items: int
    new: int
    duplicates: int
    events: list[str]
    urgent_events: list[str]
    companies_queued: list[str]


@router.post(
    "/news",
    response_model=ManualNewsOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ADMIN_ERRS,
    summary="Добавить новость вручную (пресс-релиз, сообщение аналитика): тот же конвейер, что у RSS — "
    "сырьё в S3, дедупликация, событие, матчинг, генерация, уведомления",
)
async def add_news(
    body: ManualNewsIn, request: Request, admin: Admin, conn: Conn, settings: SettingsDep
) -> ManualNewsOut:
    import hashlib

    from app.core.s3 import S3ObjectStore, get_s3_client
    from app.services.news_ingest import ingest_manual
    from app.services.pipeline import match_new_events

    items = []
    for item in body.items:
        digest = hashlib.sha256(f"{item.title}\n{item.body}".encode()).hexdigest()[:16]
        items.append({"id": f"manual-{digest}", **item.model_dump(mode="json")})
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_news_raw)
    result = await ingest_manual(conn, store, items)
    run = result.sources[0]
    companies = await match_new_events(conn, settings, result.event_ids)
    if companies:
        await request.app.state.enqueue(companies)
    return ManualNewsOut(
        outcome=run.outcome,
        items=run.n_items,
        new=run.n_new,
        duplicates=run.n_duplicates,
        events=result.event_ids,
        urgent_events=result.urgent_event_ids,
        companies_queued=companies,
    )


class AnalyticsUploadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    file_name: str
    title: str
    status: Literal["processing", "ready"]
    queued: bool = Field(description="Поставлен в очередь на обработку (false — такой файл уже загружен)")
    duplicate: bool
    task_id: str | None


@router.post(
    "/analytics",
    response_model=AnalyticsUploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **ADMIN_ERRS,
        413: {"description": "Файл больше ANALYTICS_MAX_BYTES"},
        415: {"description": "Тело запроса — не application/pdf"},
        422: {"description": "Не PDF или неверное имя файла"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    summary="Загрузить PDF аналитики (тело — файл, `Content-Type: application/pdf`): сразу виден со статусом "
    "processing, воркер извлекает текст или распознаёт сканы, режет на фрагменты и индексирует",
)
async def upload_analytics(
    request: Request,
    admin: Admin,
    conn: Conn,
    settings: SettingsDep,
    file_name: Annotated[str, Query(min_length=5, max_length=200, pattern=r"(?i)^[^/\\]+\.pdf$")],
) -> AnalyticsUploadOut:
    from app.services.analytics_upload import register_upload
    from app.workers.tasks import ingest_upload_task

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        raise ProblemError(415, "unsupported-media-type", "Expected application/pdf", content_type or None)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > settings.analytics_max_bytes:
            raise ProblemError(
                413, "too-large", "PDF is too large", f"limit {settings.analytics_max_bytes} bytes"
            )
    try:
        doc = await register_upload(conn, settings, bytes(data), file_name)
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid PDF", str(exc)) from exc
    task_id = (
        (await ingest_upload_task.kiq(document_id=str(doc["document_id"]))).task_id if doc["queued"] else None
    )
    return AnalyticsUploadOut(**doc, task_id=task_id)


class AdminAnalyticsDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    title: str
    file_name: str
    origin: str
    status: str = Field(description="processing | ready | failed")
    error: str | None
    pages: int
    chunks: int
    extraction: str | None
    ocr_confidence: float | None
    created_at: datetime
    processed_at: datetime | None


@router.get(
    "/analytics",
    response_model=list[AdminAnalyticsDocumentOut],
    responses=ADMIN_ERRS,
    summary="Все материалы аналитики со статусом обработки и ошибкой (пользователям видны только готовые)",
)
async def list_analytics(admin: Admin, conn: Conn) -> list[AdminAnalyticsDocumentOut]:
    from app.services.analytics_upload import list_all_documents

    return [AdminAnalyticsDocumentOut(**d) for d in await list_all_documents(conn)]


class GenerationRunsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="insight | qa")
    outcome: str = Field(description="completed, rejected, skipped, failed, answered… или running")
    count: int
    p50_s: float | None
    p95_s: float | None


class StuckRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    company_id: str
    event_id: uuid.UUID | None
    generator: str
    started_at: datetime


class CandidateFailureOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    event_id: uuid.UUID
    role: str
    status: str
    status_reason: str | None
    updated_at: datetime


class GenerationOverviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generator: str = Field(description="remote (ML-сервис) | stub | fake")
    ml_service: str = Field(description="ok | unavailable | http-NNN | not-used")
    queue_length: int | None = Field(description="Задач в очереди воркера (null — Redis недоступен)")
    candidates: dict[str, int] = Field(description="Кандидаты (компания × событие × роль) по статусам")
    runs_24h: list[GenerationRunsRow]
    stuck_after_s: int
    stuck_runs: list[StuckRunOut] = Field(description="Запуски без результата дольше stuck_after_s")
    recent_failures: list[CandidateFailureOut]


@router.get(
    "/generation",
    response_model=GenerationOverviewOut,
    responses=ADMIN_ERRS,
    summary="Монитор генерации: очередь, кандидаты по статусам, запуски агента за сутки (p50/p95), зависшие "
    "запуски, последние ошибки, доступность ML-сервиса",
)
async def generation_overview(admin: Admin, conn: Conn, settings: SettingsDep) -> GenerationOverviewOut:
    from app.services.generation_monitor import overview

    return GenerationOverviewOut(**await overview(conn, settings))


class GenerationRetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_ids: list[str] | None = Field(default=None, max_length=100, description="null — все компании")
    include_rejected: bool = Field(default=False, description="Вернуть в очередь и отклонённые валидатором")


class GenerationRetryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stuck_closed: int
    requeued: int
    companies: list[str]


@router.post(
    "/generation/retry",
    response_model=GenerationRetryOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ADMIN_ERRS,
    summary="Повторить генерацию: закрыть зависшие запуски, вернуть упавших кандидатов в очередь и запустить",
)
async def generation_retry(
    body: GenerationRetryIn, request: Request, admin: Admin, conn: Conn, settings: SettingsDep
) -> GenerationRetryOut:
    from app.services.generation_monitor import retry

    result = await retry(conn, settings, body.company_ids, body.include_rejected)
    if result["companies"]:
        await request.app.state.enqueue(result["companies"])
    return GenerationRetryOut(**result)


class AlertOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str


@router.get(
    "/alerts",
    response_model=list[AlertOut],
    responses=ADMIN_ERRS,
    summary="Активные проблемы сейчас: упавшие источники, бюджет LLM, доля отклонённых инсайтов, зависшие "
    "запуски, недоступный ML. Те же условия раз в 5 минут уходят в Telegram (ALERTS_PROVIDER=telegram) "
    "или в лог",
)
async def alerts(admin: Admin, conn: Conn, settings: SettingsDep) -> list[AlertOut]:
    from app.services.alerts import evaluate

    return [
        AlertOut(key=a.key, severity=a.severity, title=a.title, detail=a.detail)
        for a in await evaluate(conn, settings)
    ]


class FeedCleanupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mixed_events_split: int = Field(description="События, где демо-лента смешалась с реальными новостями")
    news_retagged: int
    events_checked: int
    liveblog_events_retired: int = Field(description="События из онлайн-трансляций сняты с ленты")
    cbr_events_merged: int = Field(description="Дубли событий о ключевой ставке ЦБ, склеенные в одно")
    cbr_events_kept: int
    events_repaired: int
    companies_to_regenerate: list[str]


@router.post(
    "/news/cleanup",
    response_model=FeedCleanupOut,
    responses=ADMIN_ERRS,
    summary="Привести накопленные новости и события к текущим правилам (ADR-040): перетегировать, снять "
    "онлайн-трансляции, склеить события об одном решении ЦБ, перегенерировать их инсайты",
)
async def news_cleanup(
    request: Request,
    admin: Admin,
    conn: Conn,
    settings: SettingsDep,
    days: Annotated[int, Query(ge=1, le=60, description="За сколько последних дней")] = 14,
) -> FeedCleanupOut:
    from app.services.feed_cleanup import cleanup

    stats = await cleanup(conn, settings, days)
    if stats["companies_to_regenerate"]:
        await request.app.state.enqueue(stats["companies_to_regenerate"])
    return FeedCleanupOut(**stats)


class MarketRefreshOut(BaseModel):
    points: dict[str, int] = Field(description="Сколько значений получено по каждому индикатору")
    errors: dict[str, str] = Field(description="Индикаторы, которые не удалось обновить, и причина")


@router.post(
    "/market/refresh",
    response_model=MarketRefreshOut,
    responses=ADMIN_ERRS,
    summary="Обновить ключевую ставку и курсы Банка России сейчас (иначе — по расписанию, ADR-043)",
)
async def market_refresh(admin: Admin, conn: Conn, settings: SettingsDep) -> MarketRefreshOut:
    from app.integrations.cbr import CbrClient
    from app.services.market import refresh

    client = CbrClient(settings.news_user_agent)
    try:
        result = await refresh(conn, client, settings.market_data_history_days)
    finally:
        await client.aclose()
    return MarketRefreshOut(points=result.points, errors=result.errors)
