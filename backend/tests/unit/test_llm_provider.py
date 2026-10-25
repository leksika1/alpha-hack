"""Validate upstream responses and optional-field compatibility behind the gateway."""

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.integrations.llm import LlmProviderError, OpenAICompatibleProvider


def provider(response: httpx.Response) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "https://provider.test/v1",
        "test-key",
        timeout_s=1,
        max_retries=0,
        transport=httpx.MockTransport(lambda request: response),
    )


def completion(message: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"choices": [{"message": message}], **extra}


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {"error": {"message": "provider failed"}},
        {"choices": []},
        {"choices": {}},
        {"choices": [None]},
        {"choices": [{"message": []}]},
        completion({}),
        completion({"content": ["not a chat completion string"]}),
        completion({"content": "answer", "tool_calls": {}}),
        completion({"content": None, "tool_calls": [None]}),
        completion({"content": None, "tool_calls": [{"type": {}, "function": {}}]}),
        completion({"content": None, "tool_calls": [{"type": "function", "function": []}]}),
        completion({"content": None, "function_call": {"name": "get_company", "arguments": {}}}),
        completion({"content": "answer"}, usage=[]),
        completion({"content": "answer"}, usage={"prompt_tokens": "100"}),
        completion({"content": "answer"}, usage={"prompt_tokens": None}),
        completion({"content": "answer"}, usage={"completion_tokens": None}),
        completion({"content": "answer"}, usage={"completion_tokens": -1}),
        completion({"content": "answer"}, usage={"completion_tokens": True}),
        completion({"content": "answer"}, usage={"completion_tokens": 1.5}),
        completion({"content": "answer"}, usage={"total_tokens": 2**31}),
    ],
)
async def test_malformed_completion_is_an_upstream_error(body: Any) -> None:
    with pytest.raises(LlmProviderError) as error:
        await provider(httpx.Response(200, json=body)).complete({"messages": []})
    assert error.value.status == 502
    assert error.value.code == "llm-invalid-response"


@pytest.mark.parametrize("operation", ["complete", "key_info"])
async def test_non_json_response_is_an_upstream_error_without_body_disclosure(operation: str) -> None:
    client = provider(httpx.Response(200, text="<html>secret-provider-key</html>"))
    with pytest.raises(LlmProviderError) as error:
        if operation == "complete":
            await client.complete({"messages": []})
        else:
            await client.key_info()
    assert error.value.status == 502
    assert "secret-provider-key" not in error.value.detail


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": "Ответ на русском"},
        {"role": "assistant", "content": None, "refusal": "Cannot answer"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "get_company", "arguments": "{}"}}
            ],
        },
        {"role": "assistant", "function_call": {"name": "get_company", "arguments": "{}"}},
        {
            "role": "assistant",
            "tool_calls": [{"id": "call-2", "type": "custom", "custom": {"name": "query", "input": "abc"}}],
        },
    ],
)
@pytest.mark.parametrize(
    "usage", [None, {}, {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}]
)
async def test_valid_completions_keep_nullable_content_and_tool_calls(
    message: dict, usage: dict | None
) -> None:
    body = completion(message, usage=usage)
    assert await provider(httpx.Response(200, json=body)).complete({"messages": []}) == body


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {"info": []},
        {"info": None},
        {"info": {"spend": "12.5"}},
        {"info": {"spend": True}},
        {"info": {"max_budget": -1}},
        {"spend": 10**400},
    ],
)
async def test_malformed_key_info_is_an_upstream_error(body: Any) -> None:
    with pytest.raises(LlmProviderError) as error:
        await provider(httpx.Response(200, json=body)).key_info()
    assert error.value.status == 502
    assert error.value.code == "llm-invalid-response"


@pytest.mark.parametrize("spend", ["NaN", "Infinity"])
async def test_nonfinite_key_info_is_an_upstream_error(spend: str) -> None:
    response = httpx.Response(200, text='{"info":{"spend":' + spend + "}}")
    with pytest.raises(LlmProviderError) as error:
        await provider(response).key_info()
    assert error.value.status == 502


@pytest.mark.parametrize(
    "body",
    [
        {"info": {"spend": 12.5, "max_budget": 500, "budget_duration": "1d"}},
        {"spend": 0, "max_budget": None},
    ],
)
async def test_key_info_accepts_nested_and_flat_metadata(body: dict) -> None:
    assert await provider(httpx.Response(200, json=body)).key_info() == body


async def test_provider_stream_is_still_relayed() -> None:
    response = httpx.Response(
        200, text='data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n'
    )
    chunks = [chunk async for chunk in provider(response).stream({"messages": []})]
    assert b"".join(chunks) == response.content


@pytest.mark.parametrize("field", ["chat_template_kwargs", "response_format"])
async def test_unsupported_optional_field_is_dropped_without_mutating_the_request(field: str) -> None:
    payload = {
        "messages": [{"role": "user", "content": "question"}],
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
    }
    original = json.loads(json.dumps(payload))
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if field in body:
            return httpx.Response(400, json={"error": {"message": f"Unsupported parameter: {field}"}})
        return httpx.Response(200, json=completion({"content": "{}"}))

    client = OpenAICompatibleProvider("https://provider.test/v1", "k", 1, 0, httpx.MockTransport(handle))
    assert (await client.complete(payload))["choices"][0]["message"]["content"] == "{}"
    assert len(seen) == 2 and seen[1] == {key: value for key, value in payload.items() if key != field}
    assert payload == original


async def test_optional_field_drops_do_not_consume_transient_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("app.integrations.llm.asyncio.sleep", AsyncMock())

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(503)
        for field in ("chat_template_kwargs", "response_format"):
            if field in body:
                return httpx.Response(400, json={"detail": f"Unknown field: {field}"})
        return httpx.Response(200, json=completion({"content": "answer"}))

    client = OpenAICompatibleProvider("https://provider.test/v1", "k", 1, 1, httpx.MockTransport(handle))
    body = {"messages": [], "chat_template_kwargs": {"enable_thinking": False}, "response_format": {}}
    assert (await client.complete(body))["choices"][0]["message"]["content"] == "answer"
    assert len(seen) == 4 and seen[0] == seen[1]
    assert set(seen[2]) == {"messages", "response_format"} and seen[3] == {"messages": []}


@pytest.mark.parametrize(
    "detail", ["invalid messages", "response_format has invalid schema", "unsupported model"]
)
async def test_unrelated_bad_request_is_not_retried_by_dropping_fields(detail: str) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"detail": detail})

    client = OpenAICompatibleProvider("https://provider.test/v1", "k", 1, 2, httpx.MockTransport(handle))
    with pytest.raises(LlmProviderError) as error:
        await client.complete({"messages": [], "response_format": {}, "chat_template_kwargs": {}})
    assert error.value.status == 502 and error.value.code == "llm-upstream-error" and calls == 1
