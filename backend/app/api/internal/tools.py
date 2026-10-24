"""Tools API for the ML agent: /internal/tools/* (contracts/tools-api.openapi.yaml).

Run-scoped token + X-Run-Id; company-bound tools only for the run's company; read-only except log_step;
every call (including refused ones) is written to core.tool_audit_log; per-run call budget.
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.api.deps import Conn, RunScope, Scope, SettingsDep
from app.core.errors import ProblemError, problem_body
from app.integrations.embeddings import get_embedder
from app.services import tools as svc
from app.services.generation import add_step

router = APIRouter(prefix="/internal/tools", include_in_schema=False)

STEP_TYPES = {
    "plan",
    "tool_call",
    "retrieval",
    "reasoning",
    "llm_call",
    "self_check",
    "retry",
    "final",
    "error",
}


async def _run_tool(
    conn: Any,
    scope: RunScope,
    tool: str,
    args: dict[str, Any],
    limit: int,
    fn: Callable[[], Awaitable[tuple[int, Any]]],
) -> JSONResponse:
    started = time.perf_counter()
    status, body = 500, None
    try:
        calls = await conn.fetchval(
            "SELECT count(*) FROM core.tool_audit_log WHERE run_id = $1", scope.run_id
        )
        if calls >= limit:
            raise ProblemError(429, "rate-limited", "Tool call budget of the run is exhausted")
        status, body = await fn()
        return JSONResponse(body, status_code=status)
    except ProblemError as exc:
        status = exc.status
        body = problem_body(exc.status, exc.code, exc.title, exc.detail, f"/internal/tools/{tool}")
        return JSONResponse(body, status_code=status, media_type="application/problem+json")
    finally:
        preview = json.dumps(body, ensure_ascii=False, default=str)[:1000] if body is not None else None
        await svc.audit(
            conn,
            scope.run_id,
            tool,
            scope.company_id,
            args,
            preview,
            status,
            int((time.perf_counter() - started) * 1000),
        )


def _require_same_company(scope: RunScope, company_id: str | None) -> None:
    if company_id is not None and company_id != scope.company_id:
        raise ProblemError(
            403, "out-of-scope", "Company is outside the scope of this run", f"company_id={company_id}"
        )


def _not_found(what: str) -> ProblemError:
    return ProblemError(404, "not-found", f"{what} not found")


@router.post("/get_company_context")
async def get_company_context(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        _require_same_company(scope, args.get("company_id"))
        ctx = await svc.company_context(conn, scope.company_id, args.get("sections"))
        if ctx is None:
            raise _not_found("Company context")
        return 200, ctx

    return await _run_tool(conn, scope, "get_company_context", args, settings.tool_calls_per_run, run)


@router.post("/get_group")
async def get_group(conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)) -> Any:
    async def run() -> tuple[int, Any]:
        _require_same_company(scope, args.get("company_id"))
        grp = await svc.group(conn, scope.company_id, min(int(args.get("members_limit", 20)), 100))
        if grp is None:
            raise _not_found("Group")
        return 200, grp

    return await _run_tool(conn, scope, "get_group", args, settings.tool_calls_per_run, run)


@router.post("/get_counterparties")
async def get_counterparties(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        _require_same_company(scope, args.get("company_id"))
        direction = args.get("direction", "both")
        if direction not in {"in", "out", "both"}:
            raise ProblemError(422, "validation", "direction must be in|out|both")
        return 200, await svc.counterparties(
            conn, scope.company_id, direction, min(int(args.get("limit", 10)), 50), args.get("industry_tag")
        )

    return await _run_tool(conn, scope, "get_counterparties", args, settings.tool_calls_per_run, run)


@router.post("/get_event")
async def get_event(conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)) -> Any:
    async def run() -> tuple[int, Any]:
        try:
            ev = await svc.event(
                conn, str(args["event_id"]), min(int(args.get("max_chars", 3000)), 8000), scope.company_id
            )
        except (KeyError, ValueError) as exc:
            raise ProblemError(422, "validation", "event_id (uuid) is required") from exc
        if ev is None:
            raise _not_found("Event")
        return 200, ev

    return await _run_tool(conn, scope, "get_event", args, settings.tool_calls_per_run, run)


@router.post("/search_news")
async def search_news(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        if not str(args.get("query", "")).strip():
            raise ProblemError(422, "validation", "query is required")
        args["limit"] = min(int(args.get("limit", 10)), 30)
        args["max_chars"] = min(int(args.get("max_chars", 1500)), 4000)
        return 200, {"hits": await svc.search_news(conn, args, scope.company_id)}

    return await _run_tool(conn, scope, "search_news", args, settings.tool_calls_per_run, run)


@router.post("/search_bank_analytics")
async def search_bank_analytics(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        if not str(args.get("query", "")).strip():
            raise ProblemError(422, "validation", "query is required")
        try:
            hits = await svc.search_bank_analytics(conn, args, get_embedder(settings))
        except ValueError as exc:
            raise ProblemError(422, "validation", "Invalid filter", str(exc)) from exc
        return 200, {"hits": hits}

    return await _run_tool(conn, scope, "search_bank_analytics", args, settings.tool_calls_per_run, run)


@router.post("/get_bank_products")
async def get_bank_products(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        _require_same_company(scope, args.get("company_id"))
        return 200, {"products": await svc.bank_products(conn, list(args.get("tags") or []))}

    return await _run_tool(conn, scope, "get_bank_products", args, settings.tool_calls_per_run, run)


@router.post("/get_market_data")
async def get_market_data(
    conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)
) -> Any:
    async def run() -> tuple[int, Any]:
        raise ProblemError(
            404, "not-available", "No external market data source is connected", args.get("indicator")
        )

    return await _run_tool(conn, scope, "get_market_data", args, settings.tool_calls_per_run, run)


@router.post("/log_step")
async def log_step(conn: Conn, scope: Scope, settings: SettingsDep, args: dict[str, Any] = Body(...)) -> Any:
    async def run() -> tuple[int, Any]:
        if args.get("step_type") not in STEP_TYPES or not args.get("name") or not args.get("status"):
            raise ProblemError(422, "validation", "step_type, name and status are required")
        status = await conn.fetchval("SELECT status FROM core.agent_runs WHERE id = $1", scope.run_id)
        if status is None:
            raise _not_found("Run")
        step_id = await add_step(
            conn,
            scope.run_id,
            args["step_type"],
            str(args["name"])[:120],
            str(args["status"]),
            input_preview=(args.get("input_preview") or None) and str(args["input_preview"])[:4000],
            output_preview=(args.get("output_preview") or None) and str(args["output_preview"])[:4000],
            data=args.get("data"),
            latency_ms=args.get("latency_ms"),
            error=args.get("error"),
            model=args.get("model"),
            tokens_in=args.get("tokens_in"),
            tokens_out=args.get("tokens_out"),
            attempt=int(args.get("attempt", 1)),
            parent_step_id=args.get("parent_step_id"),
        )
        return 201, {"step_id": step_id}

    return await _run_tool(
        conn,
        scope,
        "log_step",
        {"step_type": args.get("step_type"), "name": args.get("name")},
        settings.tool_calls_per_run,
        run,
    )
