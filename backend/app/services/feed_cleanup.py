"""Bring news and events already stored up to the current tagging and clustering rules (ADR-040).

The ingestion fixes act only on new news; this pass repairs what is already in the database:
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

from app.core.logging import get_logger
from app.domain.tagging import tag_news
from app.services.news_store import CLUSTER_WINDOW, merge_numbers

log = get_logger(__name__)

CBR_SUBJECT = "cbr_key_rate"


async def _retag_news(conn: Any, since: datetime) -> tuple[int, dict[str, list[dict[str, Any]]]]:
    """Re-tags news; returns the number changed and the new tags of every news item per event."""
    rows = await conn.fetch(
        """SELECT n.id, n.title, n.body, n.tags, n.flags, n.published_at, en.event_id
           FROM core.news_items n JOIN core.event_news en ON en.news_id = n.id
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
        by_event.setdefault(str(r["event_id"]), []).append({**new, "flags": flags, "at": r["published_at"]})
    return changed, by_event


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


async def cleanup(conn: Any, days: int = 14, now: datetime | None = None) -> dict[str, Any]:
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    async with conn.transaction():
        retagged, by_event = await _retag_news(conn, since)
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
            if e not in retired and all("liveblog" in n["flags"] for n in news)
        ]
        live_retired = await _retire(conn, live, "liveblog")
        cbr = await conn.fetch(
            """SELECT id, first_seen_at, last_seen_at, n_news, tags FROM core.events
               WHERE id = ANY($1::uuid[])
                 AND (private_company_ids IS NULL OR cardinality(private_company_ids) > 0)
                 AND tags -> 'subjects' ? $2""",
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
    companies = sorted({r["company_id"] for r in result})
    stats = {
        "news_retagged": retagged,
        "events_checked": len(by_event),
        "liveblog_events_retired": live_retired,
        "cbr_events_merged": len(merged),
        "cbr_events_kept": len(canonical),
        "events_repaired": len(repaired),
        "companies_to_regenerate": companies,
    }
    log.info("feed_cleanup_done", **{k: v for k, v in stats.items() if k != "companies_to_regenerate"})
    return stats
