"""Bring news and events already stored up to the current tagging and clustering rules (ADR-040).

The ingestion fixes act only on new news; this pass repairs what is already in the database:
* splits events where the synthetic demo feed and real sources were mixed: the real news get their own
  event (a made-up figure must not stand next to a real source);
* re-tags recent news (the key rate of a decision, not of a forecast; the Bank of Russia vs other central
  banks; live blogs) and rebuilds the figures, subjects and urgency of their events;
* retires events made only of live blogs: their cards leave the feed, matching ignores the event
  (`private_company_ids = '{}'` — visible to no company);
* merges the Bank of Russia rate events of one decision (less than the clustering window apart) into one: the
  event with the decision's rate keeps the news of the others, which are retired the same way;
* puts the candidates of the repaired events back to `pending`, so their insights are regenerated.
Idempotent: a second run finds nothing to change.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.tagging import tag_news
from app.integrations.scorers import get_scorer
from app.services.matching import match_events
from app.services.news_store import CLUSTER_WINDOW, EVENT_IS_FIXTURE_SQL, merge_numbers

log = get_logger(__name__)

CBR_SUBJECT = "cbr_key_rate"


async def _retag_news(conn: Any, since: datetime) -> tuple[int, dict[str, list[dict[str, Any]]]]:
    """Re-tags news; returns the number changed and the new tags of every news item per event."""
    rows = await conn.fetch(
        """SELECT n.id, n.title, n.body, n.tags, n.flags, n.published_at, en.event_id, s.is_fixture
           FROM core.news_items n JOIN core.event_news en ON en.news_id = n.id
           JOIN core.sources s ON s.id = n.source_id
           WHERE n.published_at >= $1 ORDER BY n.published_at, n.id""",
        since,
    )
    changed = 0
    by_event: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tags = tag_news(r["title"], r["body"] or "")
        new = tags.as_dict()
        flags = sorted(set(tags.flags))
        if json.loads(r["tags"]) != new or sorted(r["flags"]) != flags:
            await conn.execute(
                "UPDATE core.news_items SET tags = $2::jsonb, flags = $3 WHERE id = $1",
                r["id"],
                json.dumps(new, ensure_ascii=False),
                flags,
            )
            changed += 1
        by_event.setdefault(str(r["event_id"]), []).append(
            {**new, "flags": flags, "at": r["published_at"], "news_id": r["id"], "title": r["title"],
             "fixture": r["is_fixture"]}
        )  # fmt: skip
    return changed, by_event


async def _split_mixed(conn: Any, by_event: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Real news clustered into a demo-feed event move to an event of their own; returns the new events."""
    created = []
    for event_id, news in list(by_event.items()):
        real = [n for n in news if not n["fixture"]]
        if not real or len(real) == len(news):
            continue
        first = real[0]
        new_id = str(
            await conn.fetchval(
                """INSERT INTO core.events (title, event_type, direction, tags, industry_tags, countries,
                       commodities, regions, first_seen_at, last_seen_at, n_news)
                   VALUES ($1, $2, $3, '{}'::jsonb, $4, $5, $6, $7, $8, $9, $10) RETURNING id""",
                first["title"],
                first["event_type"],
                first["direction"],
                sorted({t for n in real for t in n["industry_tags"]}),
                sorted({t for n in real for t in n["countries"]}),
                sorted({t for n in real for t in n["commodities"]}),
                sorted({t for n in real for t in n["regions"]}),
                min(n["at"] for n in real),
                max(n["at"] for n in real),
                len(real),
            )
        )
        await conn.execute(
            "UPDATE core.event_news SET event_id = $1::uuid WHERE news_id = ANY($2::uuid[])",
            new_id,
            [n["news_id"] for n in real],
        )
        await conn.execute(
            "UPDATE core.events SET n_news = n_news - $2 WHERE id = $1::uuid", event_id, len(real)
        )
        by_event[event_id] = [n for n in news if n["fixture"]]
        by_event[new_id] = real
        created.append(new_id)
    return created


async def _rebuild_event(conn: Any, event_id: str, news: list[dict[str, Any]]) -> None:
    numbers: list[dict[str, Any]] = []
    for n in news:
        numbers = merge_numbers(numbers, n["numbers"])
    reasons = sorted({r for n in news for r in n["urgency_reasons"]})
    event = await conn.fetchrow("SELECT tags FROM core.events WHERE id = $1::uuid", event_id)
    tags = json.loads(event["tags"])
    tags.update(numbers=numbers, subjects=sorted({s for n in news for s in n["subjects"]}))
    await conn.execute(
        """UPDATE core.events SET tags = $2::jsonb, urgency_reasons = $3, urgent = cardinality($3::text[]) > 0
           WHERE id = $1::uuid""",
        event_id,
        json.dumps(tags, ensure_ascii=False),
        reasons,
    )


async def _retire(conn: Any, event_ids: list[str], reason: str) -> int:
    """Hide events from every company: cards leave the feed, matching skips them."""
    if not event_ids:
        return 0
    await conn.execute(
        "UPDATE core.events SET private_company_ids = '{}' WHERE id = ANY($1::uuid[])", event_ids
    )
    await conn.execute(
        """UPDATE core.insights SET expires_at = now()
           WHERE event_id = ANY($1::uuid[]) AND (expires_at IS NULL OR expires_at > now())""",
        event_ids,
    )
    await conn.execute(
        """UPDATE core.candidate_matches SET status = 'skipped', status_reason = $2, updated_at = now()
           WHERE event_id = ANY($1::uuid[])""",
        event_ids,
        reason,
    )
    return len(event_ids)


def _decision_groups(events: list[Any], window: timedelta) -> list[list[Any]]:
    """Bank of Russia rate events in chains less than the clustering window apart."""
    groups: list[list[Any]] = []
    for e in sorted(events, key=lambda e: e["first_seen_at"]):
        if groups and e["first_seen_at"] - groups[-1][-1]["last_seen_at"] <= window:
            groups[-1].append(e)
        else:
            groups.append([e])
    return [g for g in groups if len(g) > 1]


def _canonical(group: list[Any]) -> Any:
    """The event with the decision's rate (the most news first), else the earliest one."""
    with_rate = [
        e for e in group if any(n.get("kind") == "key_rate" for n in json.loads(e["tags"])["numbers"])
    ]
    return max(with_rate, key=lambda e: e["n_news"]) if with_rate else group[0]


async def _merge(conn: Any, group: list[Any]) -> tuple[str, list[str]]:
    canon = _canonical(group)
    others = [str(e["id"]) for e in group if e["id"] != canon["id"]]
    await conn.execute(
        "UPDATE core.event_news SET event_id = $1 WHERE event_id = ANY($2::uuid[])", canon["id"], others
    )
    await conn.execute(
        """UPDATE core.events e SET
               n_news = (SELECT count(*) FROM core.event_news en WHERE en.event_id = e.id),
               first_seen_at = (SELECT min(n.published_at) FROM core.event_news en
                                JOIN core.news_items n ON n.id = en.news_id WHERE en.event_id = e.id),
               last_seen_at = (SELECT max(n.published_at) FROM core.event_news en
                               JOIN core.news_items n ON n.id = en.news_id WHERE en.event_id = e.id),
               urgent = true,
               urgency_reasons = ARRAY(SELECT DISTINCT unnest(e.urgency_reasons || $2::text[]))
           WHERE e.id = $1""",
        canon["id"],
        ["cbr_key_rate_decision"],
    )
    return str(canon["id"]), others


async def cleanup(
    conn: Any, settings: Settings, days: int = 14, now: datetime | None = None
) -> dict[str, Any]:
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    async with conn.transaction():
        retagged, by_event = await _retag_news(conn, since)
        split = await _split_mixed(conn, by_event)
        for event_id, news in by_event.items():
            await _rebuild_event(conn, event_id, news)
        retired = {
            str(r["id"])
            for r in await conn.fetch(
                """SELECT id FROM core.events
                   WHERE id = ANY($1::uuid[]) AND cardinality(private_company_ids) = 0""",
                list(by_event),
            )
        }
        live = [
            e
            for e, news in by_event.items()
            if e not in retired
            and all("liveblog" in n["flags"] or "not_business" in n["flags"] for n in news)
        ]
        live_retired = await _retire(conn, live, "liveblog")
        cbr = await conn.fetch(
            f"""SELECT id, first_seen_at, last_seen_at, n_news, tags FROM core.events e
               WHERE id = ANY($1::uuid[]) AND NOT {EVENT_IS_FIXTURE_SQL}
                 AND (private_company_ids IS NULL OR cardinality(private_company_ids) > 0)
                 AND tags -> 'subjects' ? $2""",  # noqa: S608 - a constant fragment
            [e for e in by_event if e not in live],
            CBR_SUBJECT,
        )
        canonical: list[str] = []
        merged: list[str] = []
        for group in _decision_groups(list(cbr), CLUSTER_WINDOW):
            canon, others = await _merge(conn, group)
            canonical.append(canon)
            merged.extend(others)
        await _retire(conn, merged, "merged")
        repaired = canonical + [e for e in by_event if e not in live and e not in merged]
        # the kept decision events now carry the decision's rate and all the news: regenerate their cards
        result = await conn.fetch(
            """UPDATE core.candidate_matches SET status = 'pending', status_reason = 'cleanup',
                   updated_at = now()
               WHERE event_id = ANY($1::uuid[]) AND status IN ('generated', 'rejected', 'failed')
               RETURNING company_id""",
            canonical,
        )
    companies = {r["company_id"] for r in result}
    fresh = [e for e in split if e not in merged]
    if fresh:  # the real news split off a demo event get their own candidates
        await match_events(
            conn,
            fresh,
            settings.match_scope,
            settings.eligible_client_types,
            min_score=settings.match_min_score,
            max_per_company=settings.match_max_per_company,
            scorer=get_scorer(settings),
        )
        pending = await conn.fetch(
            "SELECT DISTINCT company_id FROM core.candidate_matches WHERE event_id = ANY($1::uuid[]) "
            "AND status = 'pending'",
            fresh,
        )
        companies |= {r["company_id"] for r in pending}
    stats = {
        "mixed_events_split": len(split),
        "news_retagged": retagged,
        "events_checked": len(by_event),
        "liveblog_events_retired": live_retired,
        "cbr_events_merged": len(merged),
        "cbr_events_kept": len(canonical),
        "events_repaired": len(repaired),
        "companies_to_regenerate": sorted(companies),
    }
    log.info("feed_cleanup_done", **{k: v for k, v in stats.items() if k != "companies_to_regenerate"})
    return stats


async def retire_demo_feed(conn: Any, min_live_news: int, min_real_cards: int = 5) -> int:
    """The demo feed is for offline runs (NEWS_FIXTURES_ENABLED). Once the live sources have brought at
    least `min_live_news` news in a day, demo events get no new candidates, and a company's demo cards leave
    its feed as soon as it has `min_real_cards` real ones — the feed never goes empty while real cards are
    generated (ADR-049). Returns the number of demo cards and candidates taken out; repeated runs return 0."""
    live = await conn.fetchval(
        """SELECT count(*) FROM core.news_items n JOIN core.sources s ON s.id = n.source_id
           WHERE s.kind IN ('rss', 'html') AND NOT s.is_fixture AND n.fetched_at > now() - interval '1 day'"""
    )
    if live < min_live_news:
        return 0
    demo = f"SELECT id FROM core.events e WHERE {EVENT_IS_FIXTURE_SQL}"  # noqa: S608 - a constant fragment
    skipped = await conn.fetch(
        f"""UPDATE core.candidate_matches SET status = 'skipped', status_reason = 'demo_replaced_by_live',
               updated_at = now()
           WHERE status = 'pending' AND event_id IN ({demo}) RETURNING id"""  # noqa: S608
    )
    await conn.execute(
        f"""UPDATE core.events SET private_company_ids = '{{}}'
           WHERE id IN ({demo}) AND (private_company_ids IS NULL OR cardinality(private_company_ids) > 0)"""  # noqa: S608
    )
    expired = await conn.fetch(
        f"""UPDATE core.insights i SET expires_at = now()
           WHERE i.event_id IN ({demo}) AND (i.expires_at IS NULL OR i.expires_at > now())
             AND (SELECT count(*) FROM core.insights r
                  WHERE r.company_id = i.company_id AND r.event_id NOT IN ({demo})
                    AND (r.expires_at IS NULL OR r.expires_at > now())) >= $1
           RETURNING i.id""",  # noqa: S608
        min_real_cards,
    )
    if skipped or expired:
        log.info("demo_feed_retired", cards=len(expired), candidates=len(skipped), live_news=live)
    return len(skipped) + len(expired)
