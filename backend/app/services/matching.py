"""Candidate matching: events x companies x roles -> core.candidate_matches with score breakdown.

Scoring runs in Python (domain scorer with breakdown). For a new event, ``affected_companies`` first narrows
the base with one SQL pre-filter over the denormalized company_context columns (GIN on the arrays), the
graph and the context flags: a superset of the companies any scorer component can rate high enough (FR-M1,
NFR-3). With MATCH_SCOPE=all the whole client base gets candidates, otherwise companies with users.
A candidate goes (back) to ``pending`` when it is new, its score changed or the company context changed;
already generated candidates with the same inputs are left alone (idempotent).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.scoring import (
    CONSUMER_TAGS,
    MAX_WEIGHT,
    CompanyProfile,
    EventProfile,
    profile_from_context,
    score_candidate,
)
from app.domain.tagging import GENERIC_TAGS

EVENT_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class MatchStats:
    scored: int
    candidates: int
    pending: int


def event_profile(row: Any) -> EventProfile:
    tags = row["tags"]
    tags = json.loads(tags) if isinstance(tags, str) else (tags or {})
    return EventProfile(
        event_id=str(row["id"]),
        event_type=row["event_type"],
        direction=row["direction"],
        industry_tags=frozenset(row["industry_tags"]),
        countries=frozenset(row["countries"]),
        commodities=frozenset(row["commodities"]),
        regions=frozenset(row["regions"]),
        currencies=frozenset(tags.get("currencies", [])),
        company_ids=frozenset(row["company_ids"]),
        urgent=row["urgent"],
        first_seen_at=row["first_seen_at"],
    )


async def load_profiles(conn: Any, company_ids: list[str]) -> dict[str, tuple[CompanyProfile, int]]:
    # compact scorer view; contexts built before migration 0006 have none yet -> full features
    rows = await conn.fetch(
        """SELECT company_id, context_version,
                  CASE WHEN scoring_features = '{}'::jsonb THEN features ELSE scoring_features END AS features
           FROM core.company_context WHERE company_id = ANY($1::text[])""",
        company_ids,
    )
    return {
        r["company_id"]: (
            profile_from_context(r["company_id"], json.loads(r["features"])),
            r["context_version"],
        )
        for r in rows
    }


async def roles_by_company(conn: Any, company_ids: list[str]) -> dict[str, set[str]]:
    rows = await conn.fetch(
        "SELECT company_id, role FROM core.user_company_access WHERE company_id = ANY($1::text[])",
        company_ids,
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["company_id"], set()).add(r["role"])
    return out


async def match(
    conn: Any,
    company_ids: list[str],
    roles: dict[str, set[str]] | None = None,
    event_ids: list[str] | None = None,
    min_score: float = 0.2,
    max_per_company: int = 50,
    now: datetime | None = None,
) -> MatchStats:
    now = now or datetime.now(UTC)
    roles = roles if roles is not None else await roles_by_company(conn, company_ids)
    profiles = await load_profiles(conn, company_ids)
    events = await conn.fetch(
        """SELECT * FROM core.events
           WHERE ($1::uuid[] IS NULL OR id = ANY($1::uuid[])) AND last_seen_at >= $2""",
        event_ids,
        now - timedelta(days=EVENT_WINDOW_DAYS),
    )
    ev_profiles = [event_profile(e) for e in events]
    records: list[tuple[Any, ...]] = []
    scored = 0
    for cid, (profile, ctx_version) in profiles.items():
        for role in sorted(roles.get(cid, set())):
            results = []
            for ev in ev_profiles:
                res = score_candidate(profile, ev, role, now)
                scored += 1
                if res.score >= min_score:
                    results.append((res, ev))
            results.sort(key=lambda x: -x[0].score)
            for res, ev in results[:max_per_company]:
                records.append(
                    (
                        cid,
                        ev.event_id,
                        role,
                        res.score,
                        json.dumps(res.breakdown),
                        res.matched_features,
                        ev.urgent,
                        ctx_version,
                        res.scorer,
                    )
                )
    if not records:
        return MatchStats(scored=scored, candidates=0, pending=0)
    result = await conn.fetch(
        """INSERT INTO core.candidate_matches (company_id, event_id, role, score, score_breakdown,
               matched_features,
               urgent, context_version, scorer)
           SELECT r.company_id, r.event_id::uuid, r.role, r.score, r.breakdown::jsonb, f.features, r.urgent,
                  r.ctx_version, r.scorer
           FROM unnest($1::text[], $2::text[], $3::text[], $4::float8[], $5::text[], $6::text[], $7::bool[],
                       $8::int[], $9::text[])
                AS r(company_id, event_id, role, score, breakdown, features_json, urgent, ctx_version, scorer)
           CROSS JOIN LATERAL (SELECT ARRAY(SELECT jsonb_array_elements_text(r.features_json::jsonb)) AS
               features) f
           ON CONFLICT (company_id, event_id, role) DO UPDATE SET
               score = EXCLUDED.score, score_breakdown = EXCLUDED.score_breakdown,
               matched_features = EXCLUDED.matched_features, urgent = EXCLUDED.urgent,
               context_version = EXCLUDED.context_version, scorer = EXCLUDED.scorer, updated_at = now(),
               status = CASE
                   WHEN core.candidate_matches.context_version <> EXCLUDED.context_version
                        OR abs(core.candidate_matches.score - EXCLUDED.score) > 0.05
                        OR core.candidate_matches.status = 'failed'
                   THEN 'pending' ELSE core.candidate_matches.status END
           RETURNING status""",
        [r[0] for r in records],
        [r[1] for r in records],
        [r[2] for r in records],
        [r[3] for r in records],
        [r[4] for r in records],
        [json.dumps(r[5]) for r in records],
        [r[6] for r in records],
        [r[7] for r in records],
        [r[8] for r in records],
    )
    pending = sum(1 for r in result if r["status"] == "pending")
    return MatchStats(scored=scored, candidates=len(records), pending=pending)


# The only components that do not need a matching feature are the 0.2 rate fallback for monetary policy and
# regulation/bank_analytics (which require another component). The fallback alone stays below this score.
WEAK_ONLY_MAX_SCORE = MAX_WEIGHT * 0.2
DEFAULT_ROLE = "owner"  # companies without users (MATCH_SCOPE=all): role-neutral owner view
PREFILTER_BATCH = 2000

_PREFILTER_SQL = """
SELECT c.company_id FROM core.company_context c
WHERE c.client_type = ANY($9::text[]) AND (
       c.company_id = ANY($1::text[])
    OR c.industry_tags && $2::text[]
    OR c.counterparty_tags && $2::text[]
    OR c.fts_countries && $3::text[]
    OR c.commodities && $4::text[]
    OR c.region = ANY($5::text[])
    OR ($6 AND c.fx_sensitive)
    OR ($7 AND (c.rate_sensitive OR 'deposit' = ANY(c.products)
                OR (c.features -> 'sensitivities.rate_sensitive_industry' ->> 'value')::boolean))
    OR ($8 AND (c.features -> 'sensitivities.consumer_demand_dependent' ->> 'value')::boolean)
    OR c.user_provided <> '{}'::jsonb
    OR (cardinality($1::text[]) > 0 AND EXISTS (
            SELECT 1 FROM core.company_edges e
            WHERE (e.src_id = c.company_id AND e.dst_id = ANY($1::text[]))
               OR (e.dst_id = c.company_id AND e.src_id = ANY($1::text[]))))
)
ORDER BY c.company_id
"""


async def affected_companies(conn: Any, event: Any, client_types: list[str]) -> list[str]:
    """Inverted-index pre-filter for one event (row of core.events)."""
    ev = event_profile(event)
    specific = sorted(ev.industry_tags - GENERIC_TAGS)
    consumer = bool(ev.industry_tags & CONSUMER_TAGS) or ev.event_type in {"macro_stat", "labor"}
    rows = await conn.fetch(
        _PREFILTER_SQL,
        sorted(ev.company_ids),
        specific,
        sorted(ev.countries),
        sorted(ev.commodities),
        sorted(ev.regions),
        ev.event_type in {"fx", "sanctions"},
        ev.event_type == "monetary_policy",
        consumer,
        client_types,
    )
    return [r["company_id"] for r in rows]


async def match_events(
    conn: Any,
    event_ids: list[str],
    scope: str,
    client_types: list[str],
    min_score: float = 0.2,
    max_per_company: int = 50,
) -> MatchStats:
    """New/updated events -> pre-filter -> scorer -> candidates. scope: users | all (ADR-029)."""
    if min_score < WEAK_ONLY_MAX_SCORE:  # the pre-filter would not be a superset of the candidates
        raise ValueError(f"MATCH_MIN_SCORE must be >= {WEAK_ONLY_MAX_SCORE} for the pre-filter")
    with_users = await roles_by_company(
        conn,
        [
            r["company_id"]
            for r in await conn.fetch("SELECT DISTINCT company_id FROM core.user_company_access")
        ],
    )
    events = await conn.fetch("SELECT * FROM core.events WHERE id = ANY($1::uuid[])", event_ids)
    total = MatchStats(scored=0, candidates=0, pending=0)
    for event in events:
        ids = await affected_companies(conn, event, client_types)
        if scope != "all":
            ids = [c for c in ids if c in with_users]
        for i in range(0, len(ids), PREFILTER_BATCH):
            batch = ids[i : i + PREFILTER_BATCH]
            roles = {c: with_users.get(c, {DEFAULT_ROLE}) for c in batch}
            st = await match(
                conn, batch, roles, [str(event["id"])], min_score=min_score, max_per_company=max_per_company
            )
            total = MatchStats(
                total.scored + st.scored, total.candidates + st.candidates, total.pending + st.pending
            )
    return total
