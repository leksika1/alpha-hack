"""Store normalized news, tag them and attach them to events.

Phase 2 scope: exact dedup (canonical URL + text hash) and grouping by an explicit cluster key
(fixtures, or a source-provided story id). Near-duplicate clustering by embeddings comes in phase 3
behind the same entry point ``store_news``.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.domain.tagging import NewsTags, normalize_text, strip_injection, tag_news

FIXTURE_SOURCE_ID = "fixture"
TRACKING_PARAMS = re.compile(r"^(utm_|yclid|gclid|fbclid|from$|ref$)")


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


@dataclass(frozen=True, slots=True)
class StoreResult:
    inserted: int
    duplicates: int
    event_ids: list[str]
    new_event_ids: list[str]


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


async def _attach_event(conn: Any, draft: NewsDraft, tags: NewsTags, news_id: Any) -> tuple[str, bool]:
    existing = None
    if draft.cluster_key:
        existing = await conn.fetchrow("SELECT * FROM core.events WHERE cluster_key = $1", draft.cluster_key)
    if existing is None:
        row = await conn.fetchrow(
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
            json.dumps({"currencies": tags.currencies, "numbers": tags.numbers}, ensure_ascii=False),
            tags.industry_tags,
            tags.countries,
            tags.commodities,
            tags.regions,
            draft.published_at,
        )
        event_id, created = row["id"], True
    else:
        event_id, created = existing["id"], False
        old_tags = json.loads(existing["tags"])
        merged = {
            "currencies": sorted(set(old_tags.get("currencies", [])) | set(tags.currencies)),
            "numbers": old_tags.get("numbers") or tags.numbers,
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
               WHERE id = $1""",
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
    await conn.execute("INSERT INTO core.event_news (event_id, news_id) VALUES ($1, $2)", event_id, news_id)
    return str(event_id), created


async def store_news(conn: Any, source_id: str, drafts: list[NewsDraft]) -> StoreResult:
    inserted = duplicates = 0
    event_ids: list[str] = []
    new_events: list[str] = []
    async with conn.transaction():
        for d in drafts:
            canon = canonical_url(d.url)
            digest = text_hash(d.title, d.body)
            dup = await conn.fetchval(
                "SELECT 1 FROM core.news_items WHERE canonical_url = $1 OR text_hash = $2 LIMIT 1",
                canon,
                digest,
            )
            if dup:
                duplicates += 1
                continue
            tags = tag_news(d.title, d.body)
            news_id = await conn.fetchval(
                """INSERT INTO core.news_items (source_id, external_id, url, canonical_url, title, summary,
                       body,
                       publisher, published_at, text_hash, event_type, tags, flags)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
                   RETURNING id""",
                source_id,
                d.external_id,
                d.url,
                canon,
                d.title,
                _summary(d),
                d.body,
                d.publisher,
                d.published_at,
                digest,
                tags.event_type,
                json.dumps(tags.as_dict(), ensure_ascii=False),
                tags.flags,
            )
            event_id, created = await _attach_event(conn, d, tags, news_id)
            inserted += 1
            if event_id not in event_ids:
                event_ids.append(event_id)
            if created:
                new_events.append(event_id)
    return StoreResult(
        inserted=inserted, duplicates=duplicates, event_ids=event_ids, new_event_ids=new_events
    )


def read_fixture(path: Path, now: datetime | None = None) -> list[NewsDraft]:
    """Fixture news are dated relative to `now` so the demo feed stays fresh."""
    now = now or datetime.now(UTC)
    data = json.loads(path.read_text(encoding="utf-8"))
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


async def load_fixture_news(conn: Any, path: Path, now: datetime | None = None) -> StoreResult:
    await ensure_source(conn, FIXTURE_SOURCE_ID, "Демо-лента (синтетические новости)", "fixture", None, True)
    return await store_news(conn, FIXTURE_SOURCE_ID, read_fixture(path, now))
