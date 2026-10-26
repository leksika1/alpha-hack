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


# Process-wide circuit breaker (ADR-ML-007): when the provider keeps timing out or failing, every generation
# would wait for its LLM timeout and a regeneration of hundreds of pairs takes hours. After
# BREAKER_THRESHOLD consecutive transport/HTTP failures the agent stops calling the LLM for BREAKER_OPEN_S
# seconds (rules@1 answers meanwhile), then tries again.
BREAKER_THRESHOLD = 3
BREAKER_OPEN_S = 300.0
_BREAKER: dict[str, float] = {"fails": 0.0, "open_until": 0.0}


def breaker_open() -> bool:
    return time.monotonic() < _BREAKER["open_until"]


def breaker_reset() -> None:
    _BREAKER.update(fails=0.0, open_until=0.0)


def _breaker_fail() -> None:
    _BREAKER["fails"] += 1
    if _BREAKER["fails"] >= BREAKER_THRESHOLD:
        _BREAKER.update(fails=0.0, open_until=time.monotonic() + BREAKER_OPEN_S)
        log.warning("llm_circuit_open", open_s=BREAKER_OPEN_S)


class LLMError(RuntimeError):
    pass


# Reasoning models (Qwen3.6 and others) spend ~300 tokens "thinking" before the answer; with a small max_tokens the
# visible answer comes back empty and the agent fell back to rules@1. The gateway forwards extra fields to an
# OpenAI-compatible provider: `chat_template_kwargs.enable_thinking=false` switches the thinking off where the
# server supports it (vLLM/SGLang chat templates of Qwen3); a provider that rejects the field gets the request
# again without it. An empty answer cut by the length limit is retried once with a larger limit.
NO_THINK_FAMILIES = ("qwen", "glm", "deepseek", "kimi")
MAX_TOKENS_CAP = 6000
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


# Process-wide memory of models that reason too long for an interactive deadline (the organisers' provider ignores
# the thinking switch: Qwen3.6 spent 3 091 of 3 762 tokens and 54 s on reasoning). Such a model is replaced by the
# fast one (ML_MODEL_FAST) for SLOW_FOR_S; then it is tried again.
SLOW_REASONING_TOKENS = 1200
SLOW_LATENCY_MS = 35000
SLOW_FOR_S = 1800.0
_SLOW: dict[str, float] = {}


def mark_slow(alias: str, why: str) -> None:
    fam = family_of(alias) or alias
    if not is_slow(alias):
        log.warning("llm_model_slow", model=alias, reason=why, fallback_for_s=SLOW_FOR_S)
    _SLOW[fam] = time.monotonic() + SLOW_FOR_S


def is_slow(alias: str) -> bool:
    return time.monotonic() < _SLOW.get(family_of(alias) or alias, 0.0)


def pick_model(alias: str, fast: str) -> str:
    """The configured model, or the fast one while the configured model is known to reason too long."""
    return fast if fast and alias != fast and is_slow(alias) else alias


def strip_reasoning(text: str) -> str:
    text = _THINK_BLOCK.sub("", text or "")
    if "</think>" in text:  # an opening tag swallowed by the server's template
        text = text.split("</think>", 1)[1]
    return text.strip()


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
    def __init__(
        self, base_url: str, token: str, run_id: str, timeout_s: float = 25.0, no_think: bool = True
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.no_think = no_think
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
        if breaker_open():
            self.failures += 1
            left = int(_BREAKER["open_until"] - time.monotonic())
            raise LLMUnavailable(
                f"circuit_open: the provider failed {BREAKER_THRESHOLD} times in a row; retry in {left} s"
            )
        model, display = await self.resolve(alias)
        body: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.no_think and any(f in model.lower() for f in NO_THINK_FAMILIES):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {
            "X-Purpose": purpose if purpose in {"planning", "generation", "self_check", "chat"} else "other"
        }
        last: Exception | None = None
        transport_failed = False
        started = time.perf_counter()
        grown = False
        for attempt in range(4):
            try:
                resp = await self.client.post(
                    "/chat/completions", json=body, headers=headers, timeout=timeout_s or self.timeout_s
                )
            except httpx.TimeoutException as exc:
                last = LLMUnavailable(f"timeout: {exc}")
                transport_failed = True
                mark_slow(alias, "timeout")
                break  # a timeout already consumed the time budget; do not retry
            except httpx.HTTPError as exc:
                last = LLMUnavailable(str(exc))
                transport_failed = True
                continue
            if resp.status_code == 429 and "budget" in resp.text:
                self.exhausted = True
                raise BudgetExhausted(resp.text[:200])
            if resp.status_code == 400 and attempt < 2:
                # a provider without JSON mode or without the thinking switch: drop the optional field, retry
                optional = next((k for k in ("chat_template_kwargs", "response_format") if k in body), None)
                if optional is not None:
                    body.pop(optional)
                    last = LLMUnavailable(f"HTTP 400 with {optional}")
                    log.info("llm_optional_field_dropped", field=optional, detail=resp.text[:200])
                    continue
            if resp.status_code in {429, 502, 503, 504} and attempt == 0:
                last = LLMUnavailable(f"HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                self.failures += 1
                if resp.status_code >= 500 or resp.status_code == 429:
                    _breaker_fail()
                raise LLMUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
            _BREAKER["fails"] = 0.0  # the provider answered
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = strip_reasoning(str(message.get("content") or ""))
            usage = data.get("usage") or {}
            t_in, t_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            self.usage.add(display, purpose, t_in, t_out)
            self.last = {
                "model": display,
                "tokens_in": t_in,
                "tokens_out": t_out,
                "reasoning_tokens": reasoning,
                "finish_reason": choice.get("finish_reason"),
                "max_tokens": body["max_tokens"],
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cache": resp.headers.get("X-Cache"),
            }
            if reasoning >= SLOW_REASONING_TOKENS or self.last["latency_ms"] >= SLOW_LATENCY_MS:
                mark_slow(alias, f"reasoning {reasoning} tokens, {self.last['latency_ms']} ms")
            if not content and choice.get("finish_reason") == "length" and not grown:
                # the whole budget went into reasoning: one more try with room for the answer
                grown = True
                body["max_tokens"] = min(MAX_TOKENS_CAP, max(2 * int(body["max_tokens"]), 2500))
                last = LLMError("empty answer: the token limit was spent on reasoning")
                log.warning(
                    "llm_empty_after_reasoning",
                    model=display,
                    tokens_out=t_out,
                    retry_with=body["max_tokens"],
                )
                continue
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
        if transport_failed:
            _breaker_fail()
        raise last or LLMUnavailable("no answer")

    @property
    def usable(self) -> bool:
        return not self.exhausted and self.failures < 2 and not breaker_open()
