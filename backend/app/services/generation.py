"""Insight generation for pending candidates (SPEC FR-M4..M5).

For each candidate: agent run + run-scoped token -> InsightGenerator (ML service / ml-stub, which calls the
Tools API and writes its own steps) -> validator -> core.insights or core.insights_rejected.
The backend records its own steps (request, validation, persistence) in the same run trace.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import issue_run_token
from app.domain.contracts.insight import Insight, Role
from app.domain.insight_validator import ValidationContext, ValidationResult, validate_insight
from app.domain.ports import GenerationRequest, InsightGenerator, ScoreResult
from app.services.llm_gateway import budget
from app.services.notifications import notify_insight

log = get_logger(__name__)

RUN_TOKEN_GRACE_S = 60


@dataclass
class GenerationStats:
    processed: int = 0
    generated: int = 0
    skipped: int = 0
    rejected: int = 0
    failed: int = 0
    insight_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "generated": self.generated,
            "skipped": self.skipped,
            "rejected": self.rejected,
            "failed": self.failed,
        }


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:20]


def tools_access(settings: Settings, run_id: str, company_id: str) -> dict[str, Any]:
    base = settings.internal_api_url.rstrip("/")
    ttl = int(settings.ml_request_timeout_s) + RUN_TOKEN_GRACE_S
    return {
        "base_url": f"{base}/internal/tools",
        "llm_base_url": f"{base}/internal/llm",
        "token": issue_run_token(settings, run_id, company_id, ttl),
        "run_id": run_id,
    }


async def add_step(
    conn: Any,
    run_id: str,
    step_type: str,
    name: str,
    status: str = "ok",
    *,
    input_preview: str | None = None,
    output_preview: str | None = None,
    data: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    attempt: int = 1,
    parent_step_id: str | None = None,
) -> str:
    step_id: Any = await conn.fetchval(
        """INSERT INTO core.agent_steps (run_id, seq, step_type, name, parent_step_id, input_preview,
               output_preview,
               data, model, tokens_in, tokens_out, latency_ms, status, error, attempt)
           VALUES ($1::text,
                   coalesce((SELECT max(seq) FROM core.agent_steps WHERE run_id = $1::text), 0) + 1, $2, $3,
                   $4::uuid, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14)
           RETURNING id""",
        run_id,
        step_type,
        name,
        parent_step_id,
        input_preview,
        output_preview,
        json.dumps(data, ensure_ascii=False, default=str) if data is not None else None,
        model,
        tokens_in,
        tokens_out,
        latency_ms,
        status,
        error,
        attempt,
    )
    return str(step_id)


async def validation_context(conn: Any, company_id: str, event_id: str) -> ValidationContext:
    features = await conn.fetchval(
        "SELECT features FROM core.company_context WHERE company_id = $1", company_id
    )
    products = await conn.fetch("SELECT id FROM core.bank_products")
    chunk_table = await conn.fetchval("SELECT to_regclass('core.analytics_chunks') IS NOT NULL")
    known_chunks: set[str] = set()
    if chunk_table:
        known_chunks = {str(r["id"]) for r in await conn.fetch("SELECT id FROM core.analytics_chunks")}
    return ValidationContext(
        company_id=company_id,
        event_id=event_id,
        feature_paths=frozenset(json.loads(features) if features else {}),
        known_products=frozenset(r["id"] for r in products),
        chunk_exists=lambda chunk_id: chunk_id in known_chunks,
    )


async def save_insight(conn: Any, insight: Insight, payload: dict[str, Any], ttl_days: int) -> str:
    expires = insight.expires_at or (datetime.now(UTC) + timedelta(days=ttl_days))
    async with conn.transaction():
        insight_id = await conn.fetchval(
            """INSERT INTO core.insights (company_id, event_id, role_variant, title, summary, polarity,
                   urgent,
                   impact_level, impact_score, relevance_score, schema_version, trace_id, payload, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)
               ON CONFLICT (company_id, event_id, role_variant) DO UPDATE SET
                   title = EXCLUDED.title, summary = EXCLUDED.summary, polarity = EXCLUDED.polarity,
                   urgent = EXCLUDED.urgent, impact_level = EXCLUDED.impact_level,
                   impact_score = EXCLUDED.impact_score, relevance_score = EXCLUDED.relevance_score,
                   schema_version = EXCLUDED.schema_version, trace_id = EXCLUDED.trace_id,
                   payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at, created_at = now()
               RETURNING id""",
            insight.company_id,
            insight.event_id,
            insight.role_variant.value,
            insight.title,
            insight.summary,
            insight.polarity,
            insight.urgent,
            insight.impact.level.value,
            insight.impact.score,
            insight.relevance.score,
            insight.schema_version,
            insight.trace_id,
            json.dumps(payload, ensure_ascii=False),
            expires,
        )
        await conn.execute("DELETE FROM core.insight_statements WHERE insight_id = $1", insight_id)
        await conn.execute("DELETE FROM core.insight_sources WHERE insight_id = $1", insight_id)
        await conn.executemany(
            """INSERT INTO core.insight_statements (insight_id, statement_id, kind, text, confidence,
                   source_refs)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            [
                (insight_id, s.id, s.kind.value, s.text, s.confidence, list(s.source_refs))
                for s in insight.statements
            ],
        )
        await conn.executemany(
            """INSERT INTO core.insight_sources (insight_id, source_id, source_type, ref_id, url, title, page)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            [(insight_id, s.id, s.type.value, s.ref_id, s.url, s.title, s.page) for s in insight.sources],
        )
    return str(insight_id)


async def _finish_run(
    conn: Any, run_id: str, status: str, outcome: str, usage: dict[str, Any], error: str | None
) -> None:
    await conn.execute(
        """UPDATE core.agent_runs SET status = $2, outcome = $3, usage = $4::jsonb, error = $5,
               finished_at = now()
           WHERE id = $1""",
        run_id,
        status,
        outcome,
        json.dumps(usage),
        error,
    )


async def generate_one(
    conn: Any, settings: Settings, generator: InsightGenerator, candidate: Any, stats: GenerationStats
) -> None:
    run_id = new_run_id()
    company_id: str = candidate["company_id"]
    event_id = str(candidate["event_id"])
    role = Role(candidate["role"])
    request_id = f"gen:{company_id}:{event_id}:{role.value}:v{candidate['context_version']}"
    await conn.execute(
        """INSERT INTO core.agent_runs (id, kind, request_id, company_id, event_id, role, generator)
           VALUES ($1, 'insight', $2, $3, $4, $5, $6)""",
        run_id,
        request_id,
        company_id,
        candidate["event_id"],
        role.value,
        f"{settings.insight_generator}:{generator.name}",
    )
    score = ScoreResult(
        score=float(candidate["score"]),
        breakdown=json.loads(candidate["score_breakdown"]),
        matched_features=list(candidate["matched_features"]),
        scorer=candidate["scorer"],
    )
    await add_step(
        conn,
        run_id,
        "plan",
        "backend.request",
        input_preview=f"company={company_id} event={event_id} role={role.value}",
        data={
            "candidate": {
                "score": score.score,
                "breakdown": score.breakdown,
                "matched_features": score.matched_features,
            }
        },
    )
    llm_budget = await budget(conn, settings)  # exhausted -> the agent works without LLM (NFR-9)
    req = GenerationRequest(
        request_id=request_id,
        company_id=company_id,
        event_id=event_id,
        role=role,
        run_id=run_id,
        candidate=score,
        tools=tools_access(settings, run_id, company_id),
        allow_llm=not llm_budget.exhausted,
    )
    started = time.perf_counter()
    outcome = await generator.generate(req)
    latency = int((time.perf_counter() - started) * 1000)
    stats.processed += 1

    if outcome.status == "skipped":
        await add_step(
            conn, run_id, "final", "backend.skipped", output_preview=outcome.skip_reason, latency_ms=latency
        )
        await _finish_run(conn, run_id, "ok", "skipped", outcome.usage, None)
        await conn.execute(
            "UPDATE core.candidate_matches SET status = 'skipped', status_reason = $2, updated_at = "
            "now() WHERE id = $1",
            candidate["id"],
            outcome.skip_reason,
        )
        # A re-generation that the agent now skips must not leave the previous card in the feed.
        await conn.execute(
            """UPDATE core.insights SET expires_at = now()
               WHERE company_id = $1 AND event_id = $2 AND role_variant = $3
                 AND (expires_at IS NULL OR expires_at > now())""",
            company_id,
            candidate["event_id"],
            role.value,
        )
        stats.skipped += 1
        return
    if outcome.status != "completed" or outcome.raw_insight is None:
        await add_step(
            conn,
            run_id,
            "error",
            "backend.generator_failed",
            "error",
            error=outcome.error,
            latency_ms=latency,
        )
        await _finish_run(conn, run_id, "error", "failed", outcome.usage, outcome.error)
        await conn.execute(
            "UPDATE core.candidate_matches SET status = 'failed', status_reason = $2, updated_at = "
            "now() WHERE id = $1",
            candidate["id"],
            (outcome.error or "")[:500],
        )
        stats.failed += 1
        return

    ctx = await validation_context(conn, company_id, event_id)
    result: ValidationResult = validate_insight(outcome.raw_insight, ctx)
    if not result.ok or result.insight is None:
        reasons = result.reasons()
        await add_step(
            conn,
            run_id,
            "self_check",
            "backend.validate",
            "error",
            output_preview=f"{len(reasons)} problems",
            data={"problems": reasons},
        )
        await conn.execute(
            """INSERT INTO core.insights_rejected (run_id, company_id, event_id, role, reasons, payload)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)""",
            run_id,
            company_id,
            candidate["event_id"],
            role.value,
            json.dumps(reasons, ensure_ascii=False),
            json.dumps(outcome.raw_insight, ensure_ascii=False),
        )
        await _finish_run(conn, run_id, "ok", "rejected", outcome.usage, None)
        await conn.execute(
            "UPDATE core.candidate_matches SET status = 'rejected', status_reason = $2, updated_at = "
            "now() WHERE id = $1",
            candidate["id"],
            ", ".join(sorted({r["code"] for r in reasons})),
        )
        stats.rejected += 1
        log.warning("insight_rejected", run_id=run_id, reasons=[r["code"] for r in reasons])
        return

    await add_step(
        conn, run_id, "self_check", "backend.validate", output_preview="schema + business rules passed"
    )
    payload = dict(outcome.raw_insight)
    payload["trace_id"] = run_id
    insight = result.insight.model_copy(update={"trace_id": run_id})
    insight_id = await save_insight(conn, insight, payload, settings.insight_ttl_days)
    await add_step(
        conn,
        run_id,
        "final",
        "backend.persist",
        output_preview=f"insight_id={insight_id}",
        latency_ms=latency,
    )
    await _finish_run(conn, run_id, "ok", "completed", outcome.usage, None)
    await conn.execute(
        "UPDATE core.candidate_matches SET status = 'generated', status_reason = NULL, updated_at = "
        "now() WHERE id = $1",
        candidate["id"],
    )
    stats.generated += 1
    stats.insight_ids.append(insight_id)
    await notify_insight(conn, insight_id)  # urgent / high impact -> users of the company (CJM 08)


async def generate_pending(
    conn: Any,
    settings: Settings,
    generator: InsightGenerator,
    company_ids: list[str] | None = None,
    limit: int = 500,
) -> GenerationStats:
    candidates = await conn.fetch(
        """SELECT * FROM core.candidate_matches
           WHERE status = 'pending' AND ($1::text[] IS NULL OR company_id = ANY($1::text[]))
           ORDER BY urgent DESC, score DESC, id
           LIMIT $2""",
        company_ids,
        limit,
    )
    stats = GenerationStats()
    for cand in candidates:
        try:
            await generate_one(conn, settings, generator, cand, stats)
        except Exception as exc:  # one broken candidate must not stop the batch
            stats.failed += 1
            log.exception("generation_error", candidate_id=cand["id"])
            await conn.execute(
                "UPDATE core.candidate_matches SET status = 'failed', status_reason = $2 WHERE id = $1",
                cand["id"],
                repr(exc)[:500],
            )
    log.info("generation_batch_done", **stats.as_dict())
    return stats
