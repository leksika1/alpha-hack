"""Alert sinks and the cooldown (ADR-034): Telegram payload, no bot token in errors, one per period."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.integrations.alerts import Alert, LogSink, TelegramSink, format_alert, get_alert_sink
from app.services.alerts import notify

TOKEN = "123456:secret-bot-token"
ALERT = Alert("ml-unavailable", "critical", "ML-сервис недоступен", "http://ml:8200: unavailable")


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.keys.pop(key, None)


class RecordingSink:
    name = "recording"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, alert: Alert) -> None:
        if self.fail:
            raise RuntimeError("down")
        self.sent.append(alert.key)


async def test_telegram_sink_posts_message_to_chat() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    await TelegramSink(TOKEN, "-100500", "dev", httpx.MockTransport(handler)).send(ALERT)
    assert seen and seen[0].url.path == f"/bot{TOKEN}/sendMessage"
    body = json.loads(seen[0].content)
    assert body["chat_id"] == "-100500" and body["text"] == format_alert(ALERT, "dev")
    assert body["text"].startswith("🔴 [dev] ML-сервис недоступен")


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"cannot reach {r.url}")),
    ],
)
async def test_telegram_errors_never_carry_the_bot_token(handler: object) -> None:
    sink = TelegramSink(TOKEN, "1", "dev", httpx.MockTransport(handler))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as exc:
        await sink.send(ALERT)
    assert TOKEN not in str(exc.value) and "secret" not in str(exc.value)


def test_sink_falls_back_to_log_without_bot_settings() -> None:
    assert isinstance(get_alert_sink(Settings(alerts_provider="log")), LogSink)
    assert isinstance(get_alert_sink(Settings(alerts_provider="telegram")), LogSink)
    sink = get_alert_sink(
        Settings(
            alerts_provider="telegram", telegram_bot_token=TOKEN, telegram_chat_id="1", alerts_label="prod"
        )
    )
    assert isinstance(sink, TelegramSink) and sink.env == "prod"


async def test_notify_sends_each_condition_once_per_cooldown() -> None:
    redis, sink = FakeRedis(), RecordingSink()
    other = Alert("source-down:rbc", "warning", "Источник недоступен", "timeout")
    assert await notify([ALERT, other], sink, redis, 3600) == ["ml-unavailable", "source-down:rbc"]
    assert await notify([ALERT, other], sink, redis, 3600) == []
    assert sink.sent == ["ml-unavailable", "source-down:rbc"]


async def test_failed_send_is_retried_on_the_next_check() -> None:
    redis = FakeRedis()
    assert await notify([ALERT], RecordingSink(fail=True), redis, 3600) == []
    assert await notify([ALERT], RecordingSink(), redis, 3600) == ["ml-unavailable"]
