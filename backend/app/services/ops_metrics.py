"""Pipeline gauges for Prometheus, read from the database at scrape time.

News collection, generation and market data run in the worker, while /metrics is served by the API process:
counters of the worker would never reach the scrape. The tables already hold the state, so each scrape sets
the gauges from a few aggregate queries (a failed query leaves the previous values).
"""

from typing import Any

from prometheus_client import Gauge

NEWS_SOURCES = Gauge("news_sources", "Enabled news sources by health status", ["status"])
NEWS_SOURCE_SUCCESS_AGE = Gauge(
    "news_source_last_success_age_seconds", "Seconds since the last successful fetch of a source", ["source"]
)
NEWS_FETCHES = Gauge("news_fetches_last_hour", "Source fetches in the last hour by outcome", ["outcome"])
NEWS_ITEMS = Gauge("news_items_last_24h", "News stored in the last 24 hours")
EVENTS = Gauge("events_last_24h", "Events first seen in the last 24 hours")
CANDIDATES = Gauge("generation_candidates", "Event-company candidates by status", ["status"])
MARKET_AGE = Gauge("market_rate_age_days", "Days since the last value of a market indicator", ["indicator"])

HEALTH_STATUSES = ("healthy", "degraded", "down", "unknown")
CANDIDATE_STATUSES = ("pending", "generated", "rejected", "failed", "skipped")


_shown: dict[int, set[str]] = {}  # labels set on each gauge by the previous scrape


def _set_labelled(gauge: Gauge, label_values: dict[str, float], known: tuple[str, ...] = ()) -> None:
    """Known labels without rows are reported as 0, labels that disappeared are dropped."""
    values = {k: 0.0 for k in known} | label_values
    shown = _shown.get(id(gauge), set())
    for label in shown - set(values):
        gauge.remove(label)
    for label, value in values.items():
        gauge.labels(label).set(value)
    _shown[id(gauge)] = set(values)


async def refresh(conn: Any) -> None:
    sources = await conn.fetch(
        """SELECT id, health_status, extract(epoch FROM now() - last_success_at) AS age
           FROM core.sources WHERE enabled AND NOT is_fixture"""
    )
    by_status: dict[str, float] = {}
    for s in sources:
        by_status[s["health_status"]] = by_status.get(s["health_status"], 0) + 1
    _set_labelled(NEWS_SOURCES, by_status, HEALTH_STATUSES)
    _set_labelled(
        NEWS_SOURCE_SUCCESS_AGE, {s["id"]: float(s["age"]) for s in sources if s["age"] is not None}
    )
    fetches = await conn.fetch(
        """SELECT outcome, count(*) AS n FROM core.news_raw
           WHERE fetched_at > now() - interval '1 hour' GROUP BY outcome"""
    )
    _set_labelled(NEWS_FETCHES, {r["outcome"]: float(r["n"]) for r in fetches}, ("ok", "not_modified"))
    news = "SELECT count(*) FROM core.news_items WHERE fetched_at > now() - interval '1 day'"
    NEWS_ITEMS.set(await conn.fetchval(news))
    events = "SELECT count(*) FROM core.events WHERE first_seen_at > now() - interval '1 day'"
    EVENTS.set(await conn.fetchval(events))
    candidates = await conn.fetch("SELECT status, count(*) AS n FROM core.candidate_matches GROUP BY status")
    _set_labelled(CANDIDATES, {r["status"]: float(r["n"]) for r in candidates}, CANDIDATE_STATUSES)
    market = await conn.fetch(
        "SELECT indicator, current_date - max(day) AS age FROM core.market_rates GROUP BY indicator"
    )
    _set_labelled(MARKET_AGE, {r["indicator"]: float(r["age"]) for r in market})
