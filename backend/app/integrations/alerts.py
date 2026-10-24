"""Where operational alerts go: the log (default) or a Telegram chat through the Bot API (ADR-034)."""

from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TIMEOUT_S = 10.0
ICONS = {"critical": "🔴", "warning": "🟠"}


@dataclass(frozen=True, slots=True)
class Alert:
    key: str  # stable id of the condition: one message per key per cooldown
    severity: Literal["warning", "critical"]
    title: str
    detail: str


def format_alert(alert: Alert, env: str) -> str:
    return f"{ICONS[alert.severity]} [{env}] {alert.title}\n{alert.detail}"


class AlertSink(Protocol):
    name: str

    async def send(self, alert: Alert) -> None: ...


class LogSink:
    name = "log"

    def __init__(self, env: str) -> None:
        self.env = env

    async def send(self, alert: Alert) -> None:
        log.warning("alert", key=alert.key, severity=alert.severity, title=alert.title, detail=alert.detail)


class TelegramSink:
    name = "telegram"

    def __init__(
        self, token: str, chat_id: str, env: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.env = env
        self.transport = transport

    async def send(self, alert: Alert) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": format_alert(alert, self.env),
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_S, transport=self.transport) as http:
                resp = await http.post(self.url, json=payload)
        except httpx.HTTPError as exc:  # its message may carry the URL, i.e. the bot token
            raise RuntimeError(f"telegram unreachable: {type(exc).__name__}") from None
        if resp.status_code != 200:
            # The URL holds the bot token: never log it, only the status and Telegram's description
            raise RuntimeError(f"telegram answered {resp.status_code}: {resp.text[:200]}")


def label(settings: Settings) -> str:
    return settings.alerts_label or settings.app_env


def get_alert_sink(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> AlertSink:
    if settings.alerts_provider == "telegram":
        token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
        if token and settings.telegram_chat_id:
            return TelegramSink(token, settings.telegram_chat_id, label(settings), transport)
        log.warning("alerts_telegram_not_configured", fallback="log")
    return LogSink(label(settings))
