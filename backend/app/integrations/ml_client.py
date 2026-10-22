"""HTTP client for contracts/ml-service.openapi.yaml. Used for both ml-stub and the real ML service
(INSIGHT_GENERATOR=stub|remote differ only by ML_SERVICE_URL)."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.contracts.insight import SCHEMA_VERSION
from app.domain.ports import GenerationOutcome, GenerationRequest

log = get_logger(__name__)

RETRY_STATUSES = {502, 503, 504}
BACKOFF_S = (1.0, 4.0, 8.0)
TOOLS = [
    "get_company_context",
    "get_group",
    "get_counterparties",
    "search_news",
    "get_event",
    "search_bank_analytics",
    "get_bank_products",
    "get_market_data",
    "log_step",
]


@dataclass(frozen=True, slots=True)
class ToolsAccess:
    base_url: str
    llm_base_url: str
    token: str
    run_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "llm_base_url": self.llm_base_url,
            "token": self.token,
            "run_id": self.run_id,
            "allowed_tools": TOOLS,
        }


class MlServiceError(RuntimeError):
    pass


class HttpInsightGenerator:
    name = "http"

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.service_token}"},
        )

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self._client() as client:
                    resp = await client.post(path, json=body)
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                last_exc = MlServiceError(f"{path}: HTTP {resp.status_code}")
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < self.max_retries:
                await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        raise MlServiceError(f"{path}: {last_exc!r}")

    async def generate(self, request: GenerationRequest) -> GenerationOutcome:
        body: dict[str, Any] = {
            "request_id": request.request_id,
            "company_id": request.company_id,
            "event_id": request.event_id,
            "role": request.role.value,
            "mode": "sync",
            "deadline_ms": int(self.timeout_s * 1000),
            "schema_version": SCHEMA_VERSION,
            "tools": request.tools or {},
            "options": {"allow_llm": request.allow_llm},
        }
        if request.candidate is not None:
            body["candidate"] = {
                "score": request.candidate.score,
                "breakdown": request.candidate.breakdown,
                "matched_features": request.candidate.matched_features,
            }
        try:
            resp = await self._post("/v1/insights/generate", body)
        except MlServiceError as exc:
            return GenerationOutcome(status="failed", error=str(exc))
        if resp.status_code != 200:
            return GenerationOutcome(status="failed", error=f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        status = data.get("status", "failed")
        return GenerationOutcome(
            status=status,
            raw_insight=data.get("insight") if status == "completed" else None,
            skip_reason=data.get("skip_reason"),
            error=(data.get("error") or {}).get("detail") if isinstance(data.get("error"), dict) else None,
            usage={k: int(v) for k, v in (data.get("usage") or {}).items() if isinstance(v, int | float)},
        )

    async def chat_stream(self, body: dict[str, Any]) -> AsyncIterator[str]:
        """Proxy the SSE stream of /v1/chat line by line."""
        async with self._client() as client, client.stream("POST", "/v1/chat", json=body) as resp:
            if resp.status_code != 200:
                raise MlServiceError(f"/v1/chat: HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                yield line

    async def health(self) -> bool:
        try:
            async with self._client() as client:
                return (await client.get("/health")).status_code == 200
        except httpx.TransportError:
            return False
