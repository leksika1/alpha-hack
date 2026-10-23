"""Taskiq broker (Redis list queue) and scheduler (ADR-003).

A list queue keeps messages enqueued before a worker starts (e.g. the seed's pre-generation request).

Run:
    taskiq worker app.workers.broker:broker app.workers.tasks
    taskiq scheduler app.workers.broker:scheduler app.workers.tasks
"""

from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

_settings = get_settings()

QUEUE_NAME = "alfa-tasks"


def make_broker(redis_url: str, queue_name: str = QUEUE_NAME) -> ListQueueBroker:
    """The worker waits for a task with BRPOP without a deadline. redis-py >= 8 reads with a 5 s socket
    timeout by default, and taskiq-redis catches only ConnectionError: an idle worker process crashed every
    few seconds and was restarted, taking the running task with it. No read timeout here; TCP keepalive
    finds dead connections."""
    broker = ListQueueBroker(url=redis_url, queue_name=queue_name, socket_timeout=None, socket_keepalive=True)
    broker.with_result_backend(RedisAsyncResultBackend(redis_url=redis_url, result_ex_time=24 * 3600))
    return broker


broker = make_broker(_settings.redis_url)

scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])

log = get_logger("worker")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(_: TaskiqState) -> None:
    configure_logging(_settings.log_level, _settings.log_json)
    log.info("worker_started")
