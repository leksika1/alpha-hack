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
