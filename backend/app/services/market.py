"""Official market indicators of the Bank of Russia (ADR-043): stored in core.market_rates, served to the
agent (Tools API get_market_data) and to the widget (GET /market).

A failed fetch of one indicator never blocks the others; the stored series stays as it was.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from app.core.logging import get_logger
from app.integrations.cbr import INDICATORS, SOURCE_TITLE, SOURCE_URLS, CbrError

log = get_logger(__name__)

REFRESH_DAYS = 7
DEFAULT_SERIES_DAYS = 30
MAX_SERIES_DAYS = 3 * 366


class MarketSource(Protocol):
    async def series(self, code: str, date_from: date, date_to: date) -> list[tuple[date, Decimal]]: ...


@dataclass
class RefreshResult:
    points: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def refresh(
    conn: Any, source: MarketSource, history_days: int, today: date | None = None
) -> RefreshResult:
    """Loads the full history for an empty indicator, otherwise the last week (rates get revised rarely)."""
    today = today or datetime.now(UTC).date()
    result = RefreshResult()
    for code in INDICATORS:
        last = await conn.fetchval("SELECT max(day) FROM core.market_rates WHERE indicator = $1", code)
        since = today - timedelta(days=history_days if last is None else REFRESH_DAYS)
        try:
            # the Bank of Russia publishes tomorrow's exchange rate in the afternoon
            points = await source.series(code, since, today + timedelta(days=1))
        except CbrError as exc:
            result.errors[code] = str(exc)
            log.warning("market_refresh_failed", indicator=code, error=str(exc))
            continue
        await conn.executemany(
            """INSERT INTO core.market_rates (indicator, day, value) VALUES ($1, $2, $3)
               ON CONFLICT (indicator, day) DO UPDATE SET value = EXCLUDED.value, fetched_at = now()""",
            [(code, day, value) for day, value in points],
        )
        result.points[code] = len(points)
    return result


def _source(code: str) -> dict[str, Any]:
    return {"title": SOURCE_TITLE, "url": SOURCE_URLS[code]}


def _num(value: Decimal) -> float:
    return float(value.normalize())


async def series(
    conn: Any, code: str, date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any] | None:
    """A series in the shape of the Tools API contract; None when the indicator is unknown or has no data."""
    indicator = INDICATORS.get(code)
    if indicator is None:
        return None
    date_to = date_to or datetime.now(UTC).date() + timedelta(days=1)
    date_from = date_from or date_to - timedelta(days=DEFAULT_SERIES_DAYS)
    date_from = max(date_from, date_to - timedelta(days=MAX_SERIES_DAYS))
    rows = await conn.fetch(
        """SELECT day, value FROM core.market_rates
           WHERE indicator = $1 AND day BETWEEN $2 AND $3 ORDER BY day""",
        code,
        date_from,
        date_to,
    )
    if not rows:
        return None
    return {
        "indicator": code,
        "title": indicator.title,
        "unit": indicator.unit,
        "points": [{"date": r["day"].isoformat(), "value": _num(r["value"])} for r in rows],
        "source": _source(code),
    }


async def latest(conn: Any) -> list[dict[str, Any]]:
    """The last value of every indicator with the previous different one: what the widget shows."""
    rows = await conn.fetch(
        """SELECT DISTINCT ON (indicator) indicator, day, value,
                  (SELECT p.value FROM core.market_rates p
                    WHERE p.indicator = m.indicator AND p.day < m.day AND p.value <> m.value
                    ORDER BY p.day DESC LIMIT 1) AS previous_value,
                  (SELECT p.day FROM core.market_rates p
                    WHERE p.indicator = m.indicator AND p.day < m.day AND p.value <> m.value
                    ORDER BY p.day DESC LIMIT 1) AS previous_day,
                  fetched_at
           FROM core.market_rates m ORDER BY indicator, day DESC"""
    )
    items = []
    for code, indicator in INDICATORS.items():
        r = next((r for r in rows if r["indicator"] == code), None)
        if r is None:
            continue
        prev = r["previous_value"]
        items.append(
            {
                "indicator": code,
                "title": indicator.title,
                "unit": indicator.unit,
                "value": _num(r["value"]),
                "date": r["day"].isoformat(),
                "previous_value": _num(prev) if prev is not None else None,
                "previous_date": r["previous_day"].isoformat() if r["previous_day"] else None,
                "change": _num(r["value"] - prev) if prev is not None else None,
                "fetched_at": r["fetched_at"].isoformat(),
                "source": _source(code),
            }
        )
    return items
