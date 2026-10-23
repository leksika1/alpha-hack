"""The worker's queue listener survives idling longer than redis-py's default 5 s socket timeout."""

import asyncio

import pytest
from redis.asyncio import Redis

pytestmark = pytest.mark.integration

IDLE_S = 6.5  # longer than redis-py's default socket_timeout (5 s)
QUEUE = "test-broker-idle"


def test_listener_waits_past_default_socket_timeout(infra: dict[str, str]) -> None:
    # Imported here: the module builds the app's broker from settings, which must see the test Redis
    from app.workers.broker import make_broker

    async def _run() -> bytes:
        broker = make_broker(infra["REDIS_URL"], QUEUE)  # own queue: other tests leave real tasks
        listener = broker.listen()
        try:
            waiting = asyncio.create_task(anext(listener))  # type: ignore[arg-type]
            await asyncio.sleep(IDLE_S)
            assert not waiting.done(), waiting.exception() if waiting.done() else None
            async with Redis.from_url(infra["REDIS_URL"]) as redis:
                await redis.lpush(QUEUE, b"task-after-idle")
            message = await asyncio.wait_for(waiting, timeout=5)
        finally:
            await listener.aclose()  # type: ignore[attr-defined]
            await broker.connection_pool.disconnect()
        return message  # type: ignore[no-any-return]

    assert asyncio.run(_run()) == b"task-after-idle"
