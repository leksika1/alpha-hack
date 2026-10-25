"""Store normalized news, tag them and attach them to events (SPEC FR-N3..N5).

Dedup: canonical URL -> normalized-text hash -> near-duplicate (text similarity >= NEAR_DUPLICATE against
news of the last CLUSTER_WINDOW). Clustering: an explicit cluster key (fixtures / source story id) or the most
similar recent event (``app.domain.clustering``, ADR-021); otherwise a new event.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.logging import get_logger
from app.domain.clustering import NewsSignature, best_event, is_near_duplicate, signature, tag_values
from app.domain.compliance import NEWS_RULES, check_text
from app.domain.tagging import NewsTags, normalize_text, strip_injection, tag_news
from app.services.compliance import registry_entry

log = get_logger(__name__)

FIXTURE_SOURCE_ID = "fixture"
USER_SOURCE_ID = "user"  # links and texts users send (ADR-038)
TRACKING_PARAMS = re.compile(r"^(utm_|yclid|gclid|fbclid|from$|ref$)")
CLUSTER_WINDOW = timedelta(hours=72)
RECENT_LIMIT = 5000


@dataclass(frozen=True, slots=True)
class NewsDraft:
    url: str
    title: str
    body: str
    published_at: datetime
    publisher: str | None = None
    summary: str | None = None
    external_id: str | None = None
    cluster_key: str | None = None


@dataclass
class StoreResult:
    inserted: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    blocked: int = 0  # violate Russian law or come from a registry site (ADR-037): not stored
    filtered: int = 0  # live blogs: not news of one fact (ADR-040), not stored
    event_ids: list[str] = field(default_factory=list)
    new_event_ids: list[str] = field(default_factory=list)
    urgent_event_ids: list[str] = field(default_factory=list)


def blocked_reason(draft: NewsDraft) -> str | None:
    """Why a news item must not reach users (ADR-037), or None."""
    entry = registry_entry(draft.url)
    if entry is not None:
        return f"registry_source: {entry.name} ({entry.status})"
    findings = check_text(draft.title + "\n" + draft.body, NEWS_RULES)
    return f"{findings[0].code}: {findings[0].fragment}" if findings else None


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = "&".join(
        q for q in parts.query.split("&") if q and not TRACKING_PARAMS.match(q.split("=", 1)[0].lower())
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def text_hash(title: str, body: str) -> str:
    return hashlib.sha256(normalize_text(f"{title} {body}").encode()).hexdigest()


def _summary(draft: NewsDraft) -> str:
    if draft.summary:
        return draft.summary
    first = re.split(r"(?<=[.!?])\s+", strip_injection(draft.body.strip()), maxsplit=1)[0]
    return first[:500]


def _event_tags(row: Any) -> dict[str, object]:
    extra = json.loads(row["tags"]) if row["tags"] else {}
    return {
        "industry_tags": list(row["industry_tags"]),
        "countries": list(row["countries"]),
        "commodities": list(row["commodities"]),
        "regions": list(row["regions"]),
        "currencies": extra.get("currencies", []),
        "numbers": extra.get("numbers", []),
        "subjects": extra.get("subjects", []),
    }


async def ensure_source(
    conn: Any, source_id: str, name: str, kind: str, url: str | None, is_fixture: bool
) -> None:
    await conn.execute(
        """INSERT INTO core.sources (id, name, kind, url, is_fixture, health_status)
           VALUES ($1, $2, $3, $4, $5, 'healthy')
           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, url = EXCLUDED.url""",
        source_id,
        name,
        kind,
        url,
        is_fixture,
    )


# An event of real news vs one of the synthetic demo feed: never mixed, so a made-up figure never stands next
# to a real source (ADR-040)
EVENT_IS_FIXTURE_SQL = """EXISTS (SELECT 1 FROM core.event_news en
                           JOIN core.news_items n ON n.id = en.news_id
                           JOIN core.sources s ON s.id = n.source_id
                           WHERE en.event_id = e.id AND s.is_fixture)"""


class _Window:
    """Recent news and events kept in memory for one batch (dedup and clustering candidates).
    Comparisons are limited to +-CLUSTER_WINDOW around each item's own publication time, to news of the same
    kind (synthetic demo feed vs real sources) and to events still shown (not retired, ADR-040)."""

    def __init__(self, fixture: bool = False) -> None:
        self.fixture = fixture
        self.news: list[tuple[datetime, NewsSignature]] = []
        self.events: dict[str, tuple[datetime, datetime, NewsSignature]] = {}

    async def load(self, conn: Any, since: datetime, until: datetime) -> None:
        news = await conn.fetch(
            """SELECT n.title, n.summary, n.event_type, n.tags, n.published_at FROM core.news_items n
               JOIN core.sources s ON s.id = n.source_id
               WHERE n.published_at BETWEEN $1 AND $2 AND s.is_fixture = $4
               ORDER BY n.published_at DESC LIMIT $3""",
            since,
            until,
            RECENT_LIMIT,
            self.fixture,
        )
        for n in news:
            t = json.loads(n["tags"])
            sig = signature(
                n["title"], n["summary"] or "", n["event_type"] or "other", str(t.get("direction", "none")),
                tag_values(t),
            )  # fmt: skip
            self.news.append((n["published_at"], sig))
        events = await conn.fetch(
            f"""SELECT * FROM core.events e WHERE last_seen_at >= $1 AND first_seen_at <= $2
                  AND (private_company_ids IS NULL OR cardinality(private_company_ids) > 0)
                  AND {EVENT_IS_FIXTURE_SQL} = $4
                ORDER BY last_seen_at DESC LIMIT $3""",  # noqa: S608 - a constant fragment
            since,
            until,
            RECENT_LIMIT,
            self.fixture,
        )
        for e in events:
            sig = signature(
                e["title"], e["summary"] or "", e["event_type"], e["direction"], tag_values(_event_tags(e))
            )
            self.events[str(e["id"])] = (e["first_seen_at"], e["last_seen_at"], sig)

    def news_near(self, at: datetime) -> list[NewsSignature]:
        return [sig for published, sig in self.news if abs(published - at) <= CLUSTER_WINDOW]

    def events_near(self, at: datetime) -> list[tuple[str, NewsSignature]]:
        return [
            (event_id, sig)
            for event_id, (first, last, sig) in self.events.items()
            if first - CLUSTER_WINDOW <= at <= last + CLUSTER_WINDOW
        ]

    def add_news(self, at: datetime, sig: NewsSignature) -> None:
        self.news.append((at, sig))

    def touch_event(self, event_id: str, at: datetime, sig: NewsSignature) -> None:
        first, last, old = self.events.get(event_id, (at, at, sig))
        self.events[event_id] = (min(first, at), max(last, at), old)


async def _create_event(conn: Any, draft: NewsDraft, tags: NewsTags) -> str:
    event_id = await conn.fetchval(
        """INSERT INTO core.events (cluster_key, title, summary, event_type, direction, urgent,
               urgency_reasons,
               tags, industry_tags, countries, commodities, regions, first_seen_at, last_seen_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $13)
           RETURNING id""",
        draft.cluster_key,
        draft.title,
        _summary(draft),
        tags.event_type,
        tags.direction,
        tags.urgent,
        tags.urgency_reasons,
        json.dumps(
            {"currencies": tags.currencies, "numbers": tags.numbers, "subjects": tags.subjects},
            ensure_ascii=False,
        ),
        tags.industry_tags,
        tags.countries,
        tags.commodities,
        tags.regions,
        draft.published_at,
    )
    return str(event_id)


def merge_numbers(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An event keeps its first figures, except that the decision's key rate wins over a generic percent:
    comments often come before the report with the decision itself (ADR-040)."""
    if any(n.get("kind") == "key_rate" for n in old):
        return old
    if any(n.get("kind") == "key_rate" for n in new):
        return new
    return old or new


async def _join_event(conn: Any, event_id: str, draft: NewsDraft, tags: NewsTags) -> None:
    existing = await conn.fetchrow("SELECT tags FROM core.events WHERE id = $1::uuid", event_id)
    old_tags = json.loads(existing["tags"]) if existing else {}
    merged = {
        "currencies": sorted(set(old_tags.get("currencies", [])) | set(tags.currencies)),
        "numbers": merge_numbers(old_tags.get("numbers") or [], tags.numbers),
        "subjects": sorted(set(old_tags.get("subjects", [])) | set(tags.subjects)),
    }
    await conn.execute(
        """UPDATE core.events SET
               urgent = urgent OR $2,
               urgency_reasons = ARRAY(SELECT DISTINCT unnest(urgency_reasons || $3::text[])),
               industry_tags = ARRAY(SELECT DISTINCT unnest(industry_tags || $4::text[])),
               countries = ARRAY(SELECT DISTINCT unnest(countries || $5::text[])),
               commodities = ARRAY(SELECT DISTINCT unnest(commodities || $6::text[])),
               regions = ARRAY(SELECT DISTINCT unnest(regions || $7::text[])),
               tags = $8::jsonb,
               first_seen_at = least(first_seen_at, $9), last_seen_at = greatest(last_seen_at, $9),
               n_news = n_news + 1
           WHERE id = $1::uuid""",
        event_id,
        tags.urgent,
        tags.urgency_reasons,
        tags.industry_tags,
        tags.countries,
        tags.commodities,
        tags.regions,
        json.dumps(merged, ensure_ascii=False),
        draft.published_at,
    )


async def store_news(
    conn: Any, source_id: str, drafts: list[NewsDraft], raw_id: Any | None = None
) -> StoreResult:
    result = StoreResult()
    if not drafts:
        return result
    window = _Window(
        bool(await conn.fetchval("SELECT is_fixture FROM core.sources WHERE id = $1", source_id))
    )
    async with conn.transaction():
        published = [d.published_at for d in drafts]
        await window.load(conn, min(published) - CLUSTER_WINDOW, max(published) + CLUSTER_WINDOW)
        for d in drafts:
            canon = canonical_url(d.url)
            digest = text_hash(d.title, d.body)
            dup = await conn.fetchval(
                "SELECT 1 FROM core.news_items WHERE canonical_url = $1 OR text_hash = $2 LIMIT 1",
                canon,
                digest,
            )
            if dup:
                result.duplicates += 1
                continue
            blocked = blocked_reason(d)
            if blocked is not None:
                result.blocked += 1
                log.warning("news_blocked_compliance", source_id=source_id, url=d.url, reason=blocked)
                continue
            tags = tag_news(d.title, d.body)
            if "liveblog" in tags.flags:
                result.filtered += 1
                continue
            summary = _summary(d)
            sig = signature(d.title, summary, tags.event_type, tags.direction, tag_values(tags.as_dict()))
            if any(is_near_duplicate(sig, other) for other in window.news_near(d.published_at)):
                result.near_duplicates += 1
                continue
            news_id = await conn.fetchval(
                """INSERT INTO core.news_items (source_id, external_id, url, canonical_url, title, summary,
                       body,
                       publisher, published_at, text_hash, event_type, tags, flags, raw_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14)
                   RETURNING id""",
                source_id,
                d.external_id,
                d.url,
                canon,
                d.title,
                summary,
                d.body,
                d.publisher,
                d.published_at,
                digest,
                tags.event_type,
                json.dumps(tags.as_dict(), ensure_ascii=False),
                tags.flags,
                raw_id,
            )
            event_id: str | None = None
            if d.cluster_key:
                found = await conn.fetchval(
                    "SELECT id FROM core.events WHERE cluster_key = $1", d.cluster_key
                )
                event_id = str(found) if found else None
            if event_id is None and not d.cluster_key:
                event_id, _ = best_event(sig, window.events_near(d.published_at))
            if event_id is None:
                event_id = await _create_event(conn, d, tags)
                result.new_event_ids.append(event_id)
                window.touch_event(event_id, d.published_at, sig)
            else:
                await _join_event(conn, event_id, d, tags)
                if source_id != USER_SOURCE_ID:  # a real source confirms it: no longer private
                    await conn.execute(
                        "UPDATE core.events SET private_company_ids = NULL WHERE id = $1::uuid", event_id
                    )
                window.touch_event(event_id, d.published_at, sig)
            await conn.execute(
                "INSERT INTO core.event_news (event_id, news_id) VALUES ($1::uuid, $2)", event_id, news_id
            )
            window.add_news(d.published_at, sig)
            result.inserted += 1
            if event_id not in result.event_ids:
                result.event_ids.append(event_id)
            if tags.urgent and event_id not in result.urgent_event_ids:
                result.urgent_event_ids.append(event_id)
    return result


def fixture_drafts(data: dict[str, Any], now: datetime) -> list[NewsDraft]:
    """Fixture news are dated relative to `now` so the demo feed stays fresh."""
    publisher = data.get("publisher", "Демо-лента (синтетические новости)")
    return [
        NewsDraft(
            url=f"https://fixture.local/news/{item['id']}",
            title=item["title"],
            body=item["body"],
            published_at=now - timedelta(hours=float(item.get("age_hours", 1))),
            publisher=publisher,
            external_id=item["id"],
            cluster_key=item.get("cluster"),
        )
        for item in data["items"]
    ]


def read_fixture(path: Path, now: datetime | None = None) -> list[NewsDraft]:
    return fixture_drafts(json.loads(path.read_text(encoding="utf-8")), now or datetime.now(UTC))


async def load_fixture_news(conn: Any, path: Path, now: datetime | None = None) -> StoreResult:
    await ensure_source(
        conn, FIXTURE_SOURCE_ID, "Демо-лента (синтетические новости)", "fixture", str(path), True
    )
    return await store_news(conn, FIXTURE_SOURCE_ID, read_fixture(path, now))
