"""Task registry. Pipeline tasks are added phase by phase (docs/PROGRESS.md)."""

from datetime import UTC, datetime

from app.workers.broker import broker, log


@broker.task(task_name="system.heartbeat", schedule=[{"cron": "*/5 * * * *"}])
async def heartbeat() -> str:
    """Periodic liveness signal of the worker/scheduler pair."""
    now = datetime.now(UTC).isoformat()
    log.info("heartbeat", at=now)
    return now
