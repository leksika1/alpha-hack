"""LLM client against reasoning models: empty answers after thinking, the thinking switch, <think> blocks."""

import json
from typing import Any

import httpx
import pytest

from alfa_ml import llm as llm_mod


def _client(handler: Any) -> llm_mod.LLMClient:
    llm_mod.TRANSPORT = httpx.MockTransport(handler)
    return llm_mod.LLMClient("http://gw/internal/llm", "t", "run1", timeout_s=5)


def _answer(content: str, finish: str = "stop", reasoning: int = 0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 300,
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        },
    )


MODELS = httpx.Response(200, json={"data": [{"id": "qwen3.6-35b-a3b", "display_name": "Qwen3.6 35B A3B"}]})


@pytest.fixture(autouse=True)
def _reset() -> Any:
    llm_mod._MODELS_CACHE.clear()
    llm_mod.breaker_reset()
    yield
    llm_mod.TRANSPORT = None


async def test_empty_answer_after_reasoning_is_retried_with_a_larger_limit() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return MODELS
        body = json.loads(req.content)
        bodies.append(body)
        if len(bodies) == 1:
            return _answer("", finish="length", reasoning=300)
        return _answer('{"ok": true}')

    c = _client(handler)
    out = await c.complete_json(alias="qwen3.6-35b-a3b", purpose="chat", system="s", user="u", max_tokens=900)
    assert out == {"ok": True}
    assert bodies[0]["max_tokens"] == 900 and bodies[1]["max_tokens"] >= 2500
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_a_provider_without_the_thinking_switch_gets_the_request_without_it() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return MODELS
        body = json.loads(req.content)
        bodies.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"detail": "unknown field chat_template_kwargs"})
        return _answer('<think>долго думаю</think>{"a": 1}')

    c = _client(handler)
    out = await c.complete_json(alias="qwen3.6-35b-a3b", purpose="planning", system="s", user="u")
    assert out == {"a": 1}
    assert "chat_template_kwargs" not in bodies[-1] and "response_format" in bodies[-1]


def test_reasoning_blocks_are_removed() -> None:
    assert llm_mod.strip_reasoning("<think>x</think>\n{}") == "{}"
    assert llm_mod.strip_reasoning("reasoning…</think>{}") == "{}"
