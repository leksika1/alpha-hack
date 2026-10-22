"""Task registry. News/analytics pipelines are added in phases 3-4."""

import asyncio
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.pg import pg_connection
from app.integrations.generators import get_generator
from app.services.generation import generate_pending
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
