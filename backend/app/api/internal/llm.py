"""LLM Gateway for the ML agent: /internal/llm/* (contracts/llm-gateway.openapi.yaml, ADR-028).

Auth: a run-scoped token (X-Run-Id, when sent, must match it) or the ML service token (ML_SERVICE_TOKEN)."""

import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Conn, SettingsDep, bearer
from app.core.config import Settings
from app.core.errors import ProblemError
from app.core.security import TokenError, decode_token
from app.integrations.llm import get_llm_provider
from app.services import llm_gateway as svc

router = APIRouter(prefix="/internal/llm", include_in_schema=False)

Purpose = Literal["planning", "generation", "self_check", "chat", "ocr", "other"]


async def llm_caller(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_run_id: Annotated[str | None, Header()] = None,
) -> svc.Caller:
    if creds is None or not creds.credentials:
        raise ProblemError(401, "unauthorized", "Missing bearer token")
    token = creds.credentials
    if secrets.compare_digest(token.encode(), settings.ml_service_token.get_secret_value().encode()):
        return svc.Caller(kind="service", run_id=x_run_id)
    try:
        claims = decode_token(settings, token, "run")
    except TokenError as exc:
        raise ProblemError(401, "unauthorized", "Invalid run or service token", str(exc)) from exc
    if x_run_id is not None and x_run_id != claims.get("run_id"):
        raise ProblemError(403, "run-mismatch", "X-Run-Id does not match the run token")
    return svc.Caller(kind="run", run_id=claims["run_id"], company_id=claims.get("company_id"))


CallerDep = Annotated[svc.Caller, Depends(llm_caller)]


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None


class ChatRequest(BaseModel):
    """OpenAI chat/completions; parameters beyond the contract are passed through to the provider."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[Message] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None


def _headers(remaining: float, cache: str) -> dict[str, str]:
    return {"X-Cache": cache, "X-Budget-Remaining-Rub": f"{remaining:.4f}"}


@router.get("/models")
async def list_models(caller: CallerDep, settings: SettingsDep) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": m.alias,
                "display_name": m.display_name,
                "vision": m.vision,
                "price_rub_per_1m_in": m.price.rub_per_1m_in,
                "price_rub_per_1m_out": m.price.rub_per_1m_out,
            }
            for m in svc.allowed_models(settings)
        ],
    }


@router.get("/budget")
async def get_budget(caller: CallerDep, conn: Conn, settings: SettingsDep) -> dict[str, Any]:
    return (await svc.budget(conn, settings)).as_dict()


def provider_for(request: Request, settings: Settings) -> Any:
    """Tests may install a provider on app.state (e.g. with a mocked transport)."""
    return getattr(request.app.state, "llm_provider", None) or get_llm_provider(settings)


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatRequest,
    request: Request,
    caller: CallerDep,
    conn: Conn,
    settings: SettingsDep,
    x_purpose: Annotated[Purpose, Header()] = "other",
) -> JSONResponse | StreamingResponse:
    payload = body.model_dump(exclude_none=True)
    payload["messages"] = [m.model_dump(exclude_none=True) for m in body.messages]
    provider = provider_for(request, settings)
    if body.stream:
        usage_id, prep = await svc.start_stream(conn, settings, caller, payload, x_purpose)
        remaining = (await svc.budget(conn, settings)).remaining_rub
        return StreamingResponse(
            svc.stream_chat(request.app.state.pool, provider, usage_id, prep),
            media_type="text/event-stream",
            headers={**_headers(remaining, "miss"), "Cache-Control": "no-cache"},
        )
    outcome = await svc.chat(conn, settings, provider, caller, payload, x_purpose)
    return JSONResponse(
        outcome.body, headers=_headers(outcome.remaining_rub, "hit" if outcome.cache_hit else "miss")
    )
