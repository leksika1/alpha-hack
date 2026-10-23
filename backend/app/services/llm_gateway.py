"""LLM Gateway (contracts/llm-gateway.openapi.yaml, ADR-028): the only way to a generative LLM.

* allowlist of the case's models (alias -> provider id), anything else -> 403 `model-not-allowed`;
* daily budget in RUB and tokens with a hard stop: every call first *reserves* its estimated cost under an
  advisory lock (concurrent calls cannot overshoot together), the reservation is replaced by the actual cost;
* masking of PII before anything leaves the gateway; only a masked preview is stored;
* cache of deterministic requests (temperature=0), cost 0 on a hit;
* every call (ok, cached, refused, error) is a row of core.llm_usage, linked to the agent run.
"""

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter, Gauge

from app.core.config import ALLOWED_LLM_MODELS, Settings
from app.core.errors import ProblemError
from app.core.logging import get_logger
from app.domain.llm import (
    ModelPrice,
    budget_day,
    cache_key,
    cost_rub,
    estimate_input_tokens,
    estimate_tokens,
    is_cacheable,
    mask_messages,
    prompt_preview,
)
from app.integrations.llm import LlmProvider, LlmProviderError

log = get_logger(__name__)

BUDGET_LOCK_KEY = 0x11A_B0D6E7  # pg_advisory_xact_lock key of the daily budget
VISION_MODELS = {"qwen3-vl-30b-a3b-instruct"}

LLM_REQUESTS = Counter("llm_requests_total", "LLM gateway calls", ["model", "status"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "direction"])
LLM_COST = Counter("llm_cost_rub_total", "LLM spend, RUB", ["model"])
LLM_BUDGET_REMAINING = Gauge("llm_budget_remaining_rub", "Remaining daily LLM budget, RUB")
LLM_BUDGET_EXHAUSTED = Counter("llm_budget_exhausted_total", "Calls refused by the daily budget")


@dataclass(frozen=True, slots=True)
class Caller:
    kind: str  # run | service
    run_id: str | None = None
    company_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    alias: str
    display_name: str
    provider_model: str
    vision: bool
    price: ModelPrice


@dataclass(frozen=True, slots=True)
class Budget:
    day: str
    limit_rub: float
    spent_rub: float
    remaining_rub: float
    tokens_in: int
    tokens_out: int
    token_limit: int
    exhausted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day,
            "limit_rub": self.limit_rub,
            "spent_rub": round(self.spent_rub, 4),
            "remaining_rub": round(self.remaining_rub, 4),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "exhausted": self.exhausted,
        }


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    body: dict[str, Any]
    cache_hit: bool
    remaining_rub: float


def _price(settings: Settings, alias: str) -> ModelPrice:
    p_in, p_out = settings.llm_prices.get(alias, settings.llm_default_price_rub_per_1m)
    return ModelPrice(rub_per_1m_in=p_in, rub_per_1m_out=p_out)


def allowed_models(settings: Settings) -> list[ModelInfo]:
    return [
        ModelInfo(
            alias=alias,
            display_name=name,
            provider_model=settings.llm_model_map.get(alias, alias),
            vision=alias in VISION_MODELS,
            price=_price(settings, alias),
        )
        for alias, name in ALLOWED_LLM_MODELS.items()
    ]


def resolve_model(settings: Settings, alias: str) -> ModelInfo:
    found = next((m for m in allowed_models(settings) if m.alias == alias), None)
    if found is None:
        raise ProblemError(
            403,
            "model-not-allowed",
            "Model is not in the allowlist",
            f"model={alias!r}; allowed: {', '.join(ALLOWED_LLM_MODELS)}",
        )
    return found


def _today(settings: Settings, now: datetime | None = None) -> Any:
    return budget_day(now or datetime.now(UTC), settings.llm_budget_utc_offset_h)


async def budget(conn: Any, settings: Settings, now: datetime | None = None) -> Budget:
    day = _today(settings, now)
    row = await conn.fetchrow(
        """SELECT coalesce(sum(CASE WHEN status = 'pending' THEN estimated_cost_rub ELSE cost_rub END), 0)
                      AS spent,
                  coalesce(sum(tokens_in), 0) AS tin, coalesce(sum(tokens_out), 0) AS tout
           FROM core.llm_usage WHERE day = $1""",
        day,
    )
    spent = float(row["spent"])
    remaining = max(0.0, settings.llm_daily_budget_rub - spent)
    tokens_left = settings.llm_daily_token_limit - int(row["tin"]) - int(row["tout"])
    result = Budget(
        day=day.isoformat(),
        limit_rub=settings.llm_daily_budget_rub,
        spent_rub=spent,
        remaining_rub=remaining,
        tokens_in=int(row["tin"]),
        tokens_out=int(row["tout"]),
        token_limit=settings.llm_daily_token_limit,
        exhausted=remaining <= 0 or tokens_left <= 0,
    )
    LLM_BUDGET_REMAINING.set(result.remaining_rub)
    return result


async def _insert_usage(
    conn: Any, settings: Settings, caller: Caller, purpose: str, model: str, **fields: Any
) -> int:
    cols = {
        "day": _today(settings),
        "run_id": caller.run_id,
        "caller": caller.kind,
        "purpose": purpose,
        "model": model[:64],
        **fields,
    }
    if "masked" in cols:
        cols["masked"] = json.dumps(cols["masked"])
    names = ", ".join(cols)
    params = ", ".join(f"${i}" + ("::jsonb" if k == "masked" else "") for i, k in enumerate(cols, 1))
    usage_id: int = await conn.fetchval(
        f"INSERT INTO core.llm_usage ({names}) VALUES ({params}) RETURNING id",  # noqa: S608 - fixed column names
        *cols.values(),
    )
    return usage_id


async def _finalize(
    conn: Any,
    usage_id: int,
    status: str,
    tokens_in: int,
    tokens_out: int,
    cost: float,
    latency_ms: int,
    error: str | None = None,
) -> None:
    await conn.execute(
        """UPDATE core.llm_usage SET status = $2, tokens_in = $3, tokens_out = $4, cost_rub = $5,
               latency_ms = $6, error = $7, finished_at = now()
           WHERE id = $1""",
        usage_id,
        status,
        tokens_in,
        tokens_out,
        cost,
        latency_ms,
        error,
    )


async def _refuse(
    conn: Any, settings: Settings, caller: Caller, purpose: str, model: str, exc: ProblemError
) -> None:
    await _insert_usage(conn, settings, caller, purpose, model, status="refused", error=exc.code)
    LLM_REQUESTS.labels(model=model[:64], status="refused").inc()


@dataclass(frozen=True, slots=True)
class _Prepared:
    model: ModelInfo
    payload: dict[str, Any]  # what goes to the provider (masked, provider model id)
    key: str
    preview: str
    masked: dict[str, int]
    tokens_in_est: int
    tokens_out_est: int


def _prepare(settings: Settings, request: dict[str, Any]) -> _Prepared:
    model = resolve_model(settings, str(request.get("model")))
    masked_messages, counts = mask_messages(request["messages"])
    masked_request = {**request, "messages": masked_messages}
    payload = {k: v for k, v in masked_request.items() if v is not None}
    payload["model"] = model.provider_model
    return _Prepared(
        model=model,
        payload=payload,
        key=cache_key(masked_request),
        preview=prompt_preview(masked_messages),
        masked=counts,
        tokens_in_est=estimate_input_tokens(masked_messages, request.get("tools")),
        tokens_out_est=int(request.get("max_tokens") or settings.llm_default_max_tokens),
    )


async def _reserve(
    conn: Any, settings: Settings, caller: Caller, purpose: str, prep: _Prepared, stream: bool
) -> int:
    estimate = cost_rub(prep.tokens_in_est, prep.tokens_out_est, prep.model.price)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", BUDGET_LOCK_KEY)
        b = await budget(conn, settings)
        tokens_after = b.tokens_in + b.tokens_out + prep.tokens_in_est + prep.tokens_out_est
        if b.spent_rub + estimate <= b.limit_rub and tokens_after <= b.token_limit:
            return await _insert_usage(
                conn, settings, caller, purpose, prep.model.alias,
                provider_model=prep.model.provider_model, status="pending", stream=stream,
                tokens_in=prep.tokens_in_est, tokens_out=prep.tokens_out_est, estimated_cost_rub=estimate,
                request_hash=prep.key, prompt_preview=prep.preview, masked=prep.masked,
            )  # fmt: skip
    LLM_BUDGET_EXHAUSTED.inc()
    log.error(
        "llm_budget_exhausted",
        day=b.day, spent_rub=round(b.spent_rub, 4), limit_rub=b.limit_rub, estimate_rub=estimate,
        run_id=caller.run_id, model=prep.model.alias,
    )  # fmt: skip
    exc = ProblemError(
        429,
        "budget-exhausted",
        "Daily LLM budget is exhausted",
        f"spent {b.spent_rub:.2f} of {b.limit_rub:.2f} RUB, call estimate {estimate:.4f} RUB",
        extra={"remaining_rub": round(b.remaining_rub, 4)},
    )
    await _refuse(conn, settings, caller, purpose, prep.model.alias, exc)
    raise exc


async def _cache_get(conn: Any, key: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """UPDATE core.llm_cache SET hits = hits + 1 WHERE key = $1 AND expires_at > now()
           RETURNING response""",
        key,
    )
    return json.loads(row["response"]) if row else None


async def _cache_put(
    conn: Any, settings: Settings, prep: _Prepared, body: dict[str, Any], tin: int, tout: int
) -> None:
    await conn.execute(
        """INSERT INTO core.llm_cache (key, model, response, tokens_in, tokens_out, expires_at)
           VALUES ($1, $2, $3::jsonb, $4, $5, $6)
           ON CONFLICT (key) DO UPDATE SET response = EXCLUDED.response, expires_at = EXCLUDED.expires_at""",
        prep.key,
        prep.model.alias,
        json.dumps(body, ensure_ascii=False),
        tin,
        tout,
        datetime.now(UTC) + timedelta(hours=settings.llm_cache_ttl_h),
    )


def _usage_of(body: dict[str, Any], prep: _Prepared) -> tuple[int, int]:
    usage = body.get("usage") or {}
    tin = int(usage.get("prompt_tokens") or prep.tokens_in_est)
    if usage.get("completion_tokens") is not None:
        return tin, int(usage["completion_tokens"])
    text = "".join(str((c.get("message") or {}).get("content") or "") for c in body.get("choices") or [])
    return tin, estimate_tokens(text)


def _account(model: str, status: str, tin: int, tout: int, cost: float) -> None:
    LLM_REQUESTS.labels(model=model, status=status).inc()
    LLM_TOKENS.labels(model=model, direction="in").inc(tin)
    LLM_TOKENS.labels(model=model, direction="out").inc(tout)
    LLM_COST.labels(model=model).inc(cost)


async def chat(
    conn: Any,
    settings: Settings,
    provider: LlmProvider,
    caller: Caller,
    request: dict[str, Any],
    purpose: str,
) -> ChatOutcome:
    try:
        prep = _prepare(settings, request)
    except ProblemError as exc:
        await _refuse(conn, settings, caller, purpose, str(request.get("model")), exc)
        raise
    alias = prep.model.alias
    if is_cacheable(request) and (cached := await _cache_get(conn, prep.key)) is not None:
        await _insert_usage(
            conn, settings, caller, purpose, alias, provider_model=prep.model.provider_model, status="cached",
            cache_hit=True, request_hash=prep.key, prompt_preview=prep.preview, masked=prep.masked,
            latency_ms=0,
        )  # fmt: skip
        LLM_REQUESTS.labels(model=alias, status="cached").inc()
        return ChatOutcome(cached, True, (await budget(conn, settings)).remaining_rub)
    usage_id = await _reserve(conn, settings, caller, purpose, prep, stream=False)
    started = time.perf_counter()
    try:
        body = await provider.complete(prep.payload)
    except LlmProviderError as exc:
        await _finalize(
            conn, usage_id, "error", 0, 0, 0.0, int((time.perf_counter() - started) * 1000), exc.code
        )
        LLM_REQUESTS.labels(model=alias, status="error").inc()
        log.warning("llm_provider_error", model=alias, code=exc.code, detail=exc.detail[:300])
        raise ProblemError(exc.status, exc.code, "LLM provider error", exc.detail) from exc
    tin, tout = _usage_of(body, prep)
    cost = cost_rub(tin, tout, prep.model.price)
    await _finalize(conn, usage_id, "ok", tin, tout, cost, int((time.perf_counter() - started) * 1000))
    _account(alias, "ok", tin, tout, cost)
    body["model"] = alias
    if is_cacheable(request):
        await _cache_put(conn, settings, prep, body, tin, tout)
    return ChatOutcome(body, False, (await budget(conn, settings)).remaining_rub)


@dataclass
class _StreamState:
    tokens_in: int | None = None
    tokens_out: int | None = None
    chars: int = 0


def _scan_sse(line: bytes, state: _StreamState) -> None:
    text = line.decode(errors="replace").strip()
    if not text.startswith("data:") or text.endswith("[DONE]"):
        return
    try:
        chunk = json.loads(text[5:].strip())
    except json.JSONDecodeError:
        return
    if usage := chunk.get("usage"):
        state.tokens_in = usage.get("prompt_tokens", state.tokens_in)
        state.tokens_out = usage.get("completion_tokens", state.tokens_out)
    for choice in chunk.get("choices") or []:
        state.chars += len(str((choice.get("delta") or {}).get("content") or ""))


async def start_stream(
    conn: Any, settings: Settings, caller: Caller, request: dict[str, Any], purpose: str
) -> tuple[int, _Prepared]:
    """Checks and reserves before the response starts, so refusals are ordinary problem+json responses."""
    try:
        prep = _prepare(settings, request)
    except ProblemError as exc:
        await _refuse(conn, settings, caller, purpose, str(request.get("model")), exc)
        raise
    return await _reserve(conn, settings, caller, purpose, prep, stream=True), prep


async def stream_chat(
    pool: Any, provider: LlmProvider, usage_id: int, prep: _Prepared
) -> AsyncIterator[bytes]:
    """Relays the provider's SSE; the usage row is finalized when the stream ends (or breaks)."""
    state = _StreamState()
    started = time.perf_counter()
    status, error = "ok", None
    try:
        async for line in provider.stream(prep.payload):
            _scan_sse(line, state)
            yield line
    except LlmProviderError as exc:
        status, error = "error", exc.code
        problem = {
            "type": f"https://alfa-future.local/problems/{exc.code}",
            "status": exc.status,
            "detail": exc.detail,
        }
        yield f"event: error\ndata: {json.dumps(problem, ensure_ascii=False)}\n\n".encode()
    finally:
        tin = int(state.tokens_in or prep.tokens_in_est)
        tout = int(state.tokens_out if state.tokens_out is not None else estimate_tokens("x" * state.chars))
        cost = cost_rub(tin, tout, prep.model.price) if status == "ok" else 0.0
        async with pool.acquire() as conn:
            await _finalize(
                conn, usage_id, status, tin, tout, cost, int((time.perf_counter() - started) * 1000), error
            )
        _account(prep.model.alias, status, tin, tout, cost)


async def usage_summary(conn: Any, settings: Settings, days: int = 7) -> dict[str, Any]:
    """Admin view: spend per day/model/purpose and refusals (GET /admin/llm-usage)."""
    since = _today(settings) - timedelta(days=days - 1)
    rows = await conn.fetch(
        """SELECT day, model, purpose, status, count(*) AS calls, sum(tokens_in) AS tokens_in,
                  sum(tokens_out) AS tokens_out, sum(cost_rub) AS cost_rub,
                  avg(latency_ms) FILTER (WHERE status = 'ok') AS avg_latency_ms
           FROM core.llm_usage WHERE day >= $1
           GROUP BY day, model, purpose, status ORDER BY day DESC, cost_rub DESC""",
        since,
    )
    return {
        "budget": (await budget(conn, settings)).as_dict(),
        "rows": [
            {
                "date": r["day"].isoformat(),
                "model": r["model"],
                "purpose": r["purpose"],
                "status": r["status"],
                "calls": r["calls"],
                "tokens_in": int(r["tokens_in"] or 0),
                "tokens_out": int(r["tokens_out"] or 0),
                "cost_rub": round(float(r["cost_rub"] or 0), 4),
                "avg_latency_ms": round(float(r["avg_latency_ms"]), 1)
                if r["avg_latency_ms"] is not None
                else None,
            }
            for r in rows
        ],
    }
