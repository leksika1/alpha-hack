"""Operational alerts (ADR-034): one periodic check of the system state instead of hooks in every pipeline.

Conditions: news sources down, LLM budget nearly or fully spent, a high share of insights rejected by the
validator in the last hour, stuck agent runs, the ML service unreachable. The scheduler task sends each
active condition once per cooldown (a Redis key per condition); `GET /admin/alerts` shows them live.
"""

from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.alerts import Alert, AlertSink
from app.services import generation_monitor, llm_gateway

log = get_logger(__name__)

BUDGET_WARNING_SHARE = 0.8
REJECT_MIN_RUNS = 5


async def evaluate(conn: Any, settings: Settings) -> list[Alert]:
    alerts: list[Alert] = []
    down = await conn.fetch(
        """SELECT id, name, last_error FROM core.sources
           WHERE enabled AND NOT is_fixture AND health_status = 'down' ORDER BY id"""
    )
    for s in down:
        alerts.append(
            Alert(
                f"source-down:{s['id']}",
                "warning",
                f"Источник новостей недоступен: {s['name']}",
                f"{s['last_error'] or 'нет ответа'}. Опрашивается резервный, если он есть "
                "(/admin/sources/health).",
            )
        )

    budget = await llm_gateway.budget(conn, settings)
    if budget.exhausted:
        alerts.append(
            Alert(
                "llm-budget-exhausted",
                "critical",
                "Дневной бюджет LLM исчерпан",
                f"Потрачено {budget.spent_rub:.2f} из {budget.limit_rub:.0f} ₽. "
                "Агент работает без LLM до 00:00 МСК.",
            )
        )
    elif budget.limit_rub > 0 and budget.spent_rub >= BUDGET_WARNING_SHARE * budget.limit_rub:
        alerts.append(
            Alert(
                "llm-budget-low",
                "warning",
                "Бюджет LLM почти израсходован",
                f"Потрачено {budget.spent_rub:.2f} из {budget.limit_rub:.0f} ₽ "
                f"({budget.spent_rub / budget.limit_rub:.0%}).",
            )
        )

    runs = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE outcome = 'rejected') AS rejected,
                  count(*) FILTER (WHERE outcome IN ('completed', 'rejected')) AS judged
           FROM core.agent_runs WHERE kind = 'insight' AND started_at > now() - interval '1 hour'"""
    )
    if runs["judged"] >= REJECT_MIN_RUNS and runs["rejected"] / runs["judged"] >= settings.alert_reject_ratio:
        alerts.append(
            Alert(
                "insights-rejected",
                "warning",
                "Валидатор отклоняет много инсайтов",
                f"За час отклонено {runs['rejected']} из {runs['judged']}. Причины — /admin/metrics "
                "(generation.reject_reasons); передать ML-команде.",
            )
        )

    stuck = await conn.fetchval(
        "SELECT count(*) FROM core.agent_runs WHERE status = 'running' AND started_at < now() - $1::interval",
        generation_monitor.stuck_after(settings),
    )
    if stuck:
        alerts.append(
            Alert(
                "generation-stuck",
                "warning",
                "Зависшие запуски генерации",
                f"{stuck} запусков без результата. Повторить: POST /api/v1/admin/generation/retry.",
            )
        )

    ml = await generation_monitor.ml_status(settings)
    if ml not in {"ok", "not-used"}:
        alerts.append(
            Alert(
                "ml-unavailable",
                "critical",
                "ML-сервис недоступен",
                f"{settings.ml_service_url}: {ml}. Новые инсайты не генерируются, вопросы отвечают ошибкой.",
            )
        )
    return alerts


async def notify(alerts: list[Alert], sink: AlertSink, redis: Any, cooldown_s: int) -> list[str]:
    """Sends the alerts not sent within the cooldown; returns the keys sent."""
    sent: list[str] = []
    for alert in alerts:
        if not await redis.set(f"alert:{alert.key}", "1", nx=True, ex=cooldown_s):
            continue
        try:
            await sink.send(alert)
            sent.append(alert.key)
        except Exception:
            await redis.delete(f"alert:{alert.key}")  # retry on the next check
            log.exception("alert_send_failed", key=alert.key, sink=sink.name)
    return sent
