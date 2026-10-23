"""LLM Gateway client (contracts/llm-gateway.openapi.yaml): whitelist-only models, JSON answers, usage accounting.

The service never talks to an LLM provider directly. Model aliases are resolved against GET /models so that
configuration survives alias renames: a configured alias that the gateway does not know is mapped to the
whitelisted model of the same family.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from alfa_ml.log import get_logger

log = get_logger(__name__)

TRANSPORT: httpx.AsyncBaseTransport | None = None

# Case whitelist (appendix 3): family keyword -> display name used in models_used when the gateway gives none.
WHITELIST_FAMILIES: dict[str, str] = {
    "gemma": "Gemma 4 26B A4B",
    "glm": "GLM-5.3-Flash",
    "qwen3.6": "Qwen3.6 35B A3B",
    "deepseek": "DeepSeek-V4.1-Flash",
    "qwen3-vl": "Qwen3-VL-30B-A3B-Instruct",
    "kimi": "Kimi-K2.6",
}
_MODELS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MODELS_TTL_S = 600.0


class LLMError(RuntimeError):
    pass


class BudgetExhausted(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


def family_of(alias: str) -> str | None:
    a = alias.lower()
    if "vl" in a and "qwen" in a:
        return "qwen3-vl"
    for fam in ("gemma", "glm", "deepseek", "kimi"):
        if fam in a:
            return fam
    if "qwen" in a:
        return "qwen3.6"
    return None


@dataclass
class Usage:
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    models: dict[str, set[str]] = field(default_factory=dict)  # display name -> purposes

    def add(self, display: str, purpose: str, tokens_in: int, tokens_out: int) -> None:
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.models.setdefault(display, set()).add(purpose)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model answer (tolerates code fences and prose around the object)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("answer is not JSON") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"answer is not JSON: {exc}") from None
    if not isinstance(value, dict):
        raise LLMError("JSON answer is not an object")
    return value


class LLMClient:
    def __init__(self, base_url: str, token: str, run_id: str, timeout_s: float = 25.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.run_id = run_id
        self.timeout_s = timeout_s
        self.usage = Usage()
        self.exhausted = False
        self.failures = 0  # consecutive failed calls; the agent stops using the LLM for the run after 2
        self.last: dict[str, Any] = {}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "X-Run-Id": run_id},
            timeout=timeout_s,
            transport=TRANSPORT,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def models(self) -> list[dict[str, Any]]:
        cached = _MODELS_CACHE.get(self.base_url)
        if cached and time.monotonic() - cached[0] < _MODELS_TTL_S:
            return cached[1]
        try:
            resp = await self.client.get("/models")
            data = resp.json().get("data", []) if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            data = []
        _MODELS_CACHE[self.base_url] = (time.monotonic(), data)
        return data

    async def resolve(self, alias: str) -> tuple[str, str]:
        """(alias to send, display name for models_used)."""
        listed = await self.models()
        fam = family_of(alias)
        for m in listed:
            if m.get("id") == alias:
                return alias, str(m.get("display_name") or WHITELIST_FAMILIES.get(fam or "", alias))
        for m in listed:
            if fam and family_of(str(m.get("id", ""))) == fam:
                return str(m["id"]), str(m.get("display_name") or WHITELIST_FAMILIES[fam])
        return alias, WHITELIST_FAMILIES.get(fam or "", alias)

    async def complete_json(
        self,
        *,
        alias: str,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int = 1500,
        timeout_s: float | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        if self.exhausted:
            raise BudgetExhausted("daily LLM budget exhausted")
        model, display = await self.resolve(alias)
        body: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {
            "X-Purpose": purpose if purpose in {"planning", "generation", "self_check", "chat"} else "other"
        }
        last: Exception | None = None
        started = time.perf_counter()
        for attempt in range(3):
            try:
                resp = await self.client.post(
                    "/chat/completions", json=body, headers=headers, timeout=timeout_s or self.timeout_s
                )
            except httpx.TimeoutException as exc:
                last = LLMUnavailable(f"timeout: {exc}")
                break  # a timeout already consumed the time budget; do not retry
            except httpx.HTTPError as exc:
                last = LLMUnavailable(str(exc))
                continue
            if resp.status_code == 429 and "budget" in resp.text:
                self.exhausted = True
                raise BudgetExhausted(resp.text[:200])
            if resp.status_code == 400 and "response_format" in body and attempt == 0:
                body.pop(
                    "response_format"
                )  # some providers do not support JSON mode; the prompt asks for JSON
                last = LLMUnavailable("HTTP 400 with response_format")
                continue
            if resp.status_code in {429, 502, 503, 504} and attempt == 0:
                last = LLMUnavailable(f"HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                self.failures += 1
                raise LLMUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            usage = data.get("usage") or {}
            t_in, t_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            self.usage.add(display, purpose, t_in, t_out)
            self.last = {
                "model": display,
                "tokens_in": t_in,
                "tokens_out": t_out,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cache": resp.headers.get("X-Cache"),
            }
            try:
                value = extract_json(content)
                self.failures = 0
                return value
            except LLMError as exc:
                last = exc
                if attempt >= 1:
                    break
                body["messages"] = [
                    *body["messages"],
                    {"role": "assistant", "content": content[:4000]},
                    {
                        "role": "user",
                        "content": "Ответ не является корректным JSON-объектом. Верни только JSON.",
                    },
                ]
        self.failures += 1
        raise last or LLMUnavailable("no answer")

    @property
    def usable(self) -> bool:
        return not self.exhausted and self.failures < 2
