"""LLM providers behind the gateway (ADR-028): an OpenAI-compatible HTTP API (organizers' endpoint) and a
deterministic `fake` for tests/e2e (LLM_PROVIDER=fake, never the default)."""

import asyncio
import json
import sys
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


def _invalid_response(endpoint: str, detail: str) -> LlmProviderError:
    # Do not include the response body: key/info may contain the provider key or other secrets.
    return LlmProviderError(502, "llm-invalid-response", f"{endpoint}: {detail}")


def _json_object(response: httpx.Response, endpoint: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise _invalid_response(endpoint, "response is not JSON") from exc
    if not isinstance(data, dict):
        raise _invalid_response(endpoint, "expected a JSON object")
    return data


def _completion_body(response: httpx.Response) -> dict[str, Any]:
    endpoint = "chat/completions"
    data = _json_object(response, endpoint)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _invalid_response(endpoint, "choices must be a nonempty array")
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(message := choice.get("message"), dict):
            raise _invalid_response(endpoint, "each choice must contain a message object")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise _invalid_response(endpoint, "message.content must be a string or null")
        if not any(key in message for key in ("content", "tool_calls", "function_call", "refusal")):
            raise _invalid_response(endpoint, "message has no content, tool call or refusal")
        calls = message.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
                raise _invalid_response(endpoint, "message.tool_calls must be an array of objects")
            for call in calls:
                kind = call.get("type", "function")
                if not isinstance(kind, str):
                    raise _invalid_response(endpoint, "invalid message.tool_calls type")
                argument = "input" if kind == "custom" else "arguments"
                tool = call.get(kind) if kind in {"function", "custom"} else None
                if not isinstance(tool, dict) or not all(
                    isinstance(tool.get(key), str) for key in ("name", argument)
                ):
                    raise _invalid_response(endpoint, "invalid message.tool_calls entry")
        function = message.get("function_call")
        if function is not None and (
            not isinstance(function, dict)
            or not all(isinstance(function.get(key), str) for key in ("name", "arguments"))
        ):
            raise _invalid_response(endpoint, "invalid message.function_call")
    usage = data.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise _invalid_response(endpoint, "usage must be an object or null")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            # These values are stored as PostgreSQL integer and used for charging the LLM budget.
            if key in usage and (type(value) is not int or not 0 <= value <= 2_147_483_647):
                raise _invalid_response(endpoint, f"usage.{key} must be a nonnegative integer")
    return data


def _unsupported_optional_field(response: httpx.Response, payload: dict[str, Any]) -> str | None:
    """A provider may reject optional OpenAI extensions before attempting a completion."""
    if response.status_code != 400:
        return None
    detail = response.text.lower()
    if not any(
        hint in detail
        for hint in (
            "unsupported",
            "not supported",
            "unknown",
            "unrecognized",
            "unexpected",
            "not allowed",
            "extra_forbidden",
            "extra inputs are not permitted",
        )
    ):
        return None
    return next(
        (name for name in ("chat_template_kwargs", "response_format") if name in payload and name in detail),
        None,
    )


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
        data = _json_object(response, "key/info")
        info = data.get("info", data)
        if not isinstance(info, dict):
            raise _invalid_response("key/info", "info must be an object")
        for key in ("spend", "max_budget"):
            value = info.get(key)
            if value is not None and (
                type(value) not in {int, float} or not 0 <= value <= sys.float_info.max
            ):
                raise _invalid_response("key/info", f"{key} must be a nonnegative finite number or null")
        return data

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Only top-level optional fields are removed; preserve the caller's request and budget/cache key.
        body = dict(payload)
        attempt = 0
        async with self._client() as client:
            while True:
                try:
                    resp = await client.post("/chat/completions", json=body)
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        attempt += 1
                        continue
                    raise LlmProviderError(504, "llm-timeout", f"LLM provider timeout: {exc}") from exc
                except httpx.HTTPError as exc:
                    if attempt < self.max_retries:
                        attempt += 1
                        await asyncio.sleep(0.5 * attempt)
                        continue
                    raise LlmProviderError(
                        502, "llm-unavailable", f"LLM provider unreachable: {exc}"
                    ) from exc
                if optional := _unsupported_optional_field(resp, body):
                    # At most two drops, independent of transient retries; rejected requests did not generate.
                    body.pop(optional)
                    continue
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                if resp.status_code >= 400:
                    raise LlmProviderError(
                        502,
                        "llm-upstream-error",
                        f"LLM provider returned {resp.status_code}: {resp.text[:300]}",
                    )
                return _completion_body(resp)

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
