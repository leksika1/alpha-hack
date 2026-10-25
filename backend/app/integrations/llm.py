"""LLM providers behind the gateway (ADR-028): an OpenAI-compatible HTTP API (organizers' endpoint) and a
deterministic `fake` for tests/e2e (LLM_PROVIDER=fake, never the default)."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.domain.llm import estimate_input_tokens, estimate_tokens

RETRY_STATUSES = {429, 500, 502, 503, 504}


class LlmProviderError(Exception):
    """Mapped by the gateway to 502 (upstream error) or 504 (timeout)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


class LlmProvider(Protocol):
    name: str

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]: ...


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        timeout_s: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: str | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.transport = transport
        # The provider may be unreachable from the server's network (ADR-041): only its calls use the proxy
        self.proxy = proxy

    def _client(self) -> httpx.AsyncClient:
        if not self.base_url or not self.api_key:
            raise LlmProviderError(502, "llm-not-configured", "LLM_BASE_URL and LLM_API_KEY are required")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_s,
            transport=self.transport,
            proxy=None if self.transport else self.proxy,
        )

    async def key_info(self) -> dict[str, Any]:
        """The key's budget at the provider (`/key/info` next to `/v1`): spend, limit, reset time."""
        root = self.base_url.removesuffix("/v1")
        async with self._client() as client:
            try:
                response = await client.get(f"{root}/key/info")
            except httpx.TimeoutException as exc:
                raise LlmProviderError(504, "llm-timeout", f"LLM provider timeout: {exc}") from exc
            except httpx.HTTPError as exc:
                raise LlmProviderError(502, "llm-unavailable", f"LLM provider unreachable: {exc}") from exc
        if response.status_code != 200:
            raise LlmProviderError(502, "llm-upstream-error", f"key/info: HTTP {response.status_code}")
        data: dict[str, Any] = response.json()
        return data

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            for attempt in range(self.max_retries + 1):
                try:
                    resp = await client.post("/chat/completions", json=payload)
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        continue
                    raise LlmProviderError(504, "llm-timeout", f"LLM provider timeout: {exc}") from exc
                except httpx.HTTPError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise LlmProviderError(
                        502, "llm-unavailable", f"LLM provider unreachable: {exc}"
                    ) from exc
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise LlmProviderError(
                        502,
                        "llm-upstream-error",
                        f"LLM provider returned {resp.status_code}: {resp.text[:300]}",
                    )
                data: dict[str, Any] = resp.json()
                return data
        raise LlmProviderError(502, "llm-unavailable", "LLM provider failed")  # pragma: no cover

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        async with self._client() as client:
            try:
                async with client.stream("POST", "/chat/completions", json=body) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode(errors="replace")[:300]
                        raise LlmProviderError(
                            502, "llm-upstream-error", f"LLM provider returned {resp.status_code}: {text}"
                        )
                    async for line in resp.aiter_lines():
                        if line:
                            yield (line + "\n\n").encode()
            except httpx.TimeoutException as exc:
                raise LlmProviderError(504, "llm-timeout", f"LLM provider timeout: {exc}") from exc
            except httpx.HTTPError as exc:
                raise LlmProviderError(502, "llm-unavailable", f"LLM provider unreachable: {exc}") from exc


class FakeLlmProvider:
    """Deterministic OpenAI-shaped answers; usage is estimated from the text like a real tokenizer would."""

    name = "fake"

    def _answer(self, payload: dict[str, Any]) -> str:
        users = [m for m in payload.get("messages", []) if m.get("role") == "user"]
        last = users[-1].get("content") if users else ""
        text = last if isinstance(last, str) else json.dumps(last, ensure_ascii=False)
        return f"[fake-llm:{payload.get('model')}] {text[:200]}"

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        answer = self._answer(payload)
        tokens_in = estimate_input_tokens(payload.get("messages", []), payload.get("tools"))
        tokens_out = min(estimate_tokens(answer), int(payload.get("max_tokens") or 10_000))
        return {
            "id": f"fake-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model"),
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": tokens_in,
                "completion_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
            },
        }

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        full = await self.complete(payload)
        answer = full["choices"][0]["message"]["content"]
        for i in range(0, len(answer), 40):
            chunk = {
                "id": full["id"],
                "object": "chat.completion.chunk",
                "model": full["model"],
                "choices": [{"index": 0, "delta": {"content": answer[i : i + 40]}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
        final = {"id": full["id"], "object": "chat.completion.chunk", "choices": [], "usage": full["usage"]}
        yield f"data: {json.dumps(final)}\n\n".encode()
        yield b"data: [DONE]\n\n"


def get_llm_provider(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> LlmProvider:
    if settings.llm_provider == "fake":
        return FakeLlmProvider()
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    proxy = settings.llm_proxy_url.get_secret_value() if settings.llm_proxy_url else None
    return OpenAICompatibleProvider(
        settings.llm_base_url, key, settings.llm_request_timeout_s, settings.llm_max_retries, transport, proxy
    )
