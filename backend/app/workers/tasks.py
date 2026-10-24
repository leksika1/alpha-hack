"""Task registry: generation, news, analytics, digests and metrics snapshots."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.core.pg import pg_connection
from app.integrations.alerts import get_alert_sink
from app.integrations.generators import get_generator
from app.services.alerts import evaluate as evaluate_alerts
from app.services.alerts import notify as notify_alerts
from app.services.analytics_run import run_analytics_ingest
from app.services.analytics_upload import process_upload
from app.services.generation import generate_pending
from app.services.metrics import snapshot_day
from app.services.news_run import run_news_collection
from app.services.notifications import create_digests
from app.workers.broker import broker, log

READY_TIMEOUT_S = 180


@broker.task(task_name="system.heartbeat", schedule=[{"cron": "*/5 * * * *"}])
async def heartbeat() -> str:
    """Periodic liveness signal of the worker/scheduler pair."""
    now = datetime.now(UTC).isoformat()
    log.info("heartbeat", at=now)
    return now


async def _wait_ready(urls: list[str], timeout_s: int = READY_TIMEOUT_S) -> None:
    """Generation needs the ML service and the backend Tools API; wait for both after a cold start."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=5) as client:
        for url in urls:
            while True:
                try:
                    if (await client.get(url)).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError(f"{url} is not ready")
                await asyncio.sleep(2)


@broker.task(task_name="insights.generate_pending")
async def generate_pending_task(company_ids: list[str] | None = None) -> dict[str, int]:
    settings = get_settings()
    await _wait_ready(
        [f"{settings.ml_service_url.rstrip('/')}/health", f"{settings.internal_api_url.rstrip('/')}/health"]
    )
    async with pg_connection() as conn:
        stats = await generate_pending(conn, settings, get_generator(settings), company_ids)
    return stats.as_dict()


async def enqueue_generation(company_ids: list[str]) -> None:
    await generate_pending_task.kiq(company_ids=company_ids)


@broker.task(
    task_name="news.poll",
    schedule=[{"cron": f"*/{max(1, min(59, get_settings().news_poll_interval_min))} * * * *"}],
)
async def poll_news_task(source_ids: list[str] | None = None) -> dict[str, object]:
    """Scheduled news collection; new relevant events go straight to generation (urgent ones first,
    see generate_pending ordering)."""
    settings = get_settings()
    async with pg_connection() as conn:
        result = await run_news_collection(conn, settings, source_ids=source_ids)
    if result.companies_to_generate:
        await enqueue_generation(result.companies_to_generate)
    summary = result.as_dict()
    log.info("news_poll_done", **{k: v for k, v in summary.items() if k != "sources"})
    return summary


@broker.task(task_name="analytics.ingest")
async def ingest_analytics_task(force: bool = False) -> list[dict[str, object]]:
    """Ingest bank analytics PDFs (organizers' or synthetic); OCR results are cached in S3."""
    settings = get_settings()
    async with pg_connection() as conn:
        outcomes = await run_analytics_ingest(conn, settings, force=force)
    return [o.__dict__ for o in outcomes]


@broker.task(task_name="analytics.ingest_upload")
async def ingest_upload_task(document_id: str) -> dict[str, object]:
    """Index a PDF uploaded through the admin API (FR-A7)."""
    settings = get_settings()
    async with pg_connection() as conn:
        outcome = await process_upload(conn, settings, uuid.UUID(document_id))
    log.info("analytics_upload_done", document_id=document_id, status=outcome.status, error=outcome.error)
    return outcome.__dict__


@broker.task(task_name="notifications.digest_daily", schedule=[{"cron": get_settings().digest_daily_cron}])
async def digest_daily_task() -> int:
    async with pg_connection() as conn:
        return await create_digests(conn, "day")


@broker.task(task_name="notifications.digest_weekly", schedule=[{"cron": get_settings().digest_weekly_cron}])
async def digest_weekly_task() -> int:
    async with pg_connection() as conn:
        return await create_digests(conn, "week")


@broker.task(task_name="alerts.check", schedule=[{"cron": get_settings().alerts_cron}])
async def alerts_check_task() -> list[str]:
    """Sends active operational alerts (ADR-034), each at most once per cooldown."""
    import redis.asyncio as aioredis

    settings = get_settings()
    async with pg_connection() as conn:
        alerts = await evaluate_alerts(conn, settings)
    async with aioredis.from_url(settings.redis_url) as redis:
        return await notify_alerts(alerts, get_alert_sink(settings), redis, settings.alert_cooldown_min * 60)


@broker.task(task_name="metrics.snapshot", schedule=[{"cron": get_settings().metrics_daily_cron}])
async def metrics_snapshot_task(day: str | None = None) -> str:
    """Yesterday's metrics (UTC day) into core.metrics_daily."""
    target = datetime.fromisoformat(day).date() if day else (datetime.now(UTC) - timedelta(days=1)).date()
    async with pg_connection() as conn:
        await snapshot_day(conn, target)
    return target.isoformat()
