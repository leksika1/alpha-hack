"""Taskiq broker (Redis Streams, at-least-once delivery) and scheduler (ADR-003).

Run:
    taskiq worker app.workers.broker:broker app.workers.tasks
    taskiq scheduler app.workers.broker:scheduler app.workers.tasks
"""

from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

_settings = get_settings()

broker = RedisStreamBroker(url=_settings.redis_url, queue_name="alfa-tasks").with_result_backend(
    RedisAsyncResultBackend(redis_url=_settings.redis_url, result_ex_time=24 * 3600)
)

scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])

log = get_logger("worker")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(_: TaskiqState) -> None:
    configure_logging(_settings.log_level, _settings.log_json)
    log.info("worker_started")
