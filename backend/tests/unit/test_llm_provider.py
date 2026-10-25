"""Malformed HTTP 200 provider responses must release gateway reservations as upstream failures."""

from typing import Any

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
