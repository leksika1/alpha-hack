"""ML service by contracts/ml-service.openapi.yaml: /health, /v1/insights/generate, /v1/chat (SSE), /v1/score."""

import asyncio
import time
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from alfa_ml import __version__, prompts
from alfa_ml.agent.runner import Agent
from alfa_ml.chat import chat_stream
from alfa_ml.config import get_settings
from alfa_ml.log import configure, get_logger
from alfa_ml.ranker import RANKER_NAME, rescore_baseline

configure(get_settings().log_level)
log = get_logger("alfa_ml")
app = FastAPI(
    title="alfa-ml",
    version=__version__,
    description="ML-сервис: агент инсайтов, ответы на вопросы, ранжирование",
)

# Idempotency (contract: the same request_id within 24 h returns the same result).
_RESULTS: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
_INFLIGHT: dict[str, asyncio.Future[dict[str, Any]]] = {}
_TTL_S = 24 * 3600
_MAX_CACHED = 2000
METRICS: dict[str, int] = {
    "generate_completed": 0,
    "generate_skipped": 0,
    "generate_failed": 0,
    "generate_llm": 0,
    "generate_rules": 0,
    "chat": 0,
    "score_items": 0,
}


def _problem(status: int, code: str, title: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://alfa-future.local/problems/{code}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _authorized(authorization: str | None) -> bool:
    token = get_settings().ml_service_token
    return not token or authorization == f"Bearer {token}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/info")
async def info() -> dict[str, Any]:
    s = get_settings()
    return {
        "version": __version__,
        "ranker": RANKER_NAME,
        "prompts": {name: prompts.version(name) for name in ("planner", "analyst", "critic", "chat")},
        "models": {
            "planner": s.ml_model_planner,
            "analyst": s.ml_model_analyst,
            "critic": s.ml_model_critic,
            "chat": s.ml_model_chat,
        },
        "llm_enabled": s.ml_llm_enabled,
        "max_iterations": s.ml_max_iterations,
        "metrics": METRICS,
    }


async def _generate(req: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = await Agent(req).run()
    body: dict[str, Any] = {"request_id": req["request_id"], "status": result.status, "usage": result.usage}
    if result.status == "completed":
        body["insight"] = result.insight
        author = next(
            (
                m["name"]
                for m in (result.insight or {}).get("models_used", [])
                if m["purpose"] == "generation"
            ),
            None,
        )
        METRICS["generate_llm" if author else "generate_rules"] += 1
    elif result.status == "skipped":
        body["skip_reason"] = result.skip_reason
    else:
        body["error"] = {
            "type": "https://alfa-future.local/problems/ml-error",
            "title": "Generation failed",
            "status": 500,
            "detail": result.error or "unknown",
        }
    METRICS[f"generate_{result.status}"] = METRICS.get(f"generate_{result.status}", 0) + 1
    log.info(
        "infer_complete",
        request_id=req["request_id"],
        status=result.status,
        skip_reason=result.skip_reason,
        latency_ms=int((time.perf_counter() - started) * 1000),
        **{k: v for k, v in result.usage.items() if k != "latency_ms"},
    )
    return body


@app.post("/v1/insights/generate")
async def generate(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    if not _authorized(authorization):
        return _problem(401, "unauthorized", "Invalid service token")
    try:
        req = await request.json()
    except ValueError:
        return _problem(422, "validation", "Body is not JSON")
    for key in ("request_id", "company_id", "event_id", "role", "tools"):
        if key not in req:
            return _problem(422, "validation", f"{key} is required")
    tools = req["tools"]
    if not isinstance(tools, dict) or not all(k in tools for k in ("base_url", "token", "run_id")):
        return _problem(422, "validation", "tools.base_url, tools.token and tools.run_id are required")
    rid = str(req["request_id"])
    now = time.time()
    cached = _RESULTS.get(rid)
    if cached and now - cached[0] < _TTL_S:
        return JSONResponse(cached[1])
    if rid in _INFLIGHT:
        return JSONResponse(await asyncio.shield(_INFLIGHT[rid]))
    fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    _INFLIGHT[rid] = fut
    try:
        body = await _generate(req)
        fut.set_result(body)
    except Exception as exc:
        fut.set_exception(exc)
        raise
    finally:
        _INFLIGHT.pop(rid, None)
    if body["status"] != "failed":
        _RESULTS[rid] = (now, body)
        while len(_RESULTS) > _MAX_CACHED:
            _RESULTS.popitem(last=False)
    return JSONResponse(body)


@app.post("/v1/chat")
async def chat(request: Request, authorization: str | None = Header(None)) -> Any:
    if not _authorized(authorization):
        return _problem(401, "unauthorized", "Invalid service token")
    try:
        req = await request.json()
    except ValueError:
        return _problem(422, "validation", "Body is not JSON")
    if not req.get("messages") or "tools" not in req or "company_id" not in req:
        return _problem(422, "validation", "company_id, messages and tools are required")
    METRICS["chat"] += 1
    return StreamingResponse(
        chat_stream(req), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/v1/score")
async def score(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    if not _authorized(authorization):
        return _problem(401, "unauthorized", "Invalid service token")
    req = await request.json()
    items = [
        it for it in req.get("items", []) if isinstance(it, dict) and "company_id" in it and "event_id" in it
    ][:500]
    METRICS["score_items"] += len(items)
    return JSONResponse(
        {
            "request_id": req.get("request_id", ""),
            "scorer": f"{RANKER_NAME}:recalibrated-baseline",
            "scores": [rescore_baseline(it) for it in items],
        }
    )
