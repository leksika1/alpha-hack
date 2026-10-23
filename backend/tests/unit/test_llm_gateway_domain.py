from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.config import Settings, insecure_defaults
from app.core.ratelimit import bucket_for, caller_key
from app.domain.llm import (
    ModelPrice,
    budget_day,
    cache_key,
    cost_rub,
    estimate_input_tokens,
    is_cacheable,
    mask_messages,
    mask_text,
    prompt_preview,
)
from app.integrations.llm import FakeLlmProvider, LlmProviderError, OpenAICompatibleProvider


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Директор F_123456 одобрил", "Директор [PERSON] одобрил"),
        ("пишите на ivan.petrov@corp.ru сегодня", "пишите на [EMAIL] сегодня"),
        ("звоните +7 (912) 345-67-89 или 8 912 345 67 89", "звоните [PHONE] или [PHONE]"),
        ("ИНН 7707083893, счёт 40702810900000012345", "ИНН [ID], счёт [ID]"),
    ],
)
def test_pii_is_masked(text: str, expected: str) -> None:
    assert mask_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Ставка 14,0%, выручка 1 500 000 руб., рост 12% г/г",
        "Компания U_700001, ОКВЭД 24.10, 2026 год, ОФЗ 26254",
        "Курс 92.4567 руб./$, объём 3,56 трлн руб.",
    ],
)
def test_business_numbers_are_kept(text: str) -> None:
    assert mask_text(text) == text


def test_mask_messages_counts_and_handles_content_parts() -> None:
    messages = [
        {"role": "system", "content": "Ты аналитик."},
        {
            "role": "user",
            "content": [{"type": "text", "text": "F_1 и F_2 пишут a@b.ru"}, {"type": "image_url"}],
        },
    ]
    masked, counts = mask_messages(messages)
    assert masked[1]["content"][0]["text"] == "[PERSON] и [PERSON] пишут [EMAIL]"
    assert masked[1]["content"][1] == {"type": "image_url"}
    assert counts == {"person_id": 2, "email": 1}
    assert messages[1]["content"][0]["text"].startswith("F_1")  # input is not mutated


def test_estimates_cost_and_preview() -> None:
    messages = [{"role": "system", "content": "x" * 300}, {"role": "user", "content": "y" * 3000}]
    tokens = estimate_input_tokens(messages)
    assert 1000 <= tokens <= 1200
    assert cost_rub(1_000_000, 500_000, ModelPrice(20, 60)) == pytest.approx(50.0)
    assert prompt_preview(messages, 10) == "y" * 10


def test_cache_key_and_cacheability() -> None:
    req = {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "a"}], "temperature": 0}
    assert is_cacheable(req)
    assert not is_cacheable({**req, "temperature": 0.7})
    assert not is_cacheable({**req, "stream": True})
    assert not is_cacheable({k: v for k, v in req.items() if k != "temperature"})
    assert cache_key(req) == cache_key(dict(reversed(list(req.items()))))
    assert cache_key(req) != cache_key({**req, "max_tokens": 10})


def test_budget_day_is_moscow_day() -> None:
    late_utc = datetime(2026, 10, 23, 21, 30, tzinfo=UTC)  # 00:30 in Moscow
    assert budget_day(late_utc, 3).isoformat() == "2026-10-24"
    assert budget_day(late_utc, 0).isoformat() == "2026-10-23"


async def test_fake_provider_is_openai_shaped() -> None:
    fake = FakeLlmProvider()
    body = await fake.complete(
        {"model": "glm", "messages": [{"role": "user", "content": "привет"}], "max_tokens": 5}
    )
    assert body["choices"][0]["message"]["content"].startswith("[fake-llm:glm] привет")
    assert body["usage"]["completion_tokens"] <= 5
    chunks = [c async for c in fake.stream({"model": "glm", "messages": [{"role": "user", "content": "x"}]})]
    assert chunks[-1] == b"data: [DONE]\n\n" and b'"usage"' in chunks[-2]


@respx.mock
async def test_openai_provider_retries_then_maps_errors() -> None:
    route = respx.post("https://llm.test/v1/chat/completions")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={"choices": [], "usage": {}})]
    provider = OpenAICompatibleProvider("https://llm.test/v1", "k", timeout_s=5, max_retries=1)
    assert await provider.complete({"model": "m", "messages": []}) == {"choices": [], "usage": {}}
    assert route.calls[0].request.headers["authorization"] == "Bearer k"

    route.side_effect = [httpx.Response(400, text="bad request")]
    with pytest.raises(LlmProviderError) as err:
        await provider.complete({"model": "m", "messages": []})
    assert err.value.status == 502 and err.value.code == "llm-upstream-error"

    route.side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(LlmProviderError) as err:
        await provider.complete({"model": "m", "messages": []})
    assert err.value.status == 504


async def test_unconfigured_provider_fails_cleanly() -> None:
    with pytest.raises(LlmProviderError) as err:
        await OpenAICompatibleProvider(None, None, 5, 0).complete({"model": "m", "messages": []})
    assert err.value.code == "llm-not-configured"


def test_rate_limit_buckets_and_caller_keys() -> None:
    s = Settings(rate_limit_public_per_min=5, rate_limit_internal_per_min=50)
    assert bucket_for("/api/v1/feed", s) == ("public", 5)
    assert bucket_for("/internal/llm/chat/completions", s) == ("internal", 50)
    assert bucket_for("/health", s) is None and bucket_for("/metrics", s) is None
    with_token = {"headers": [(b"authorization", b"Bearer abc")], "client": ("10.0.0.1", 1)}
    without = {"headers": [], "client": ("10.0.0.1", 1)}
    assert caller_key(with_token).startswith("t:") and "abc" not in caller_key(with_token)
    assert caller_key(without) == "ip:10.0.0.1"


def test_prod_refuses_development_secrets() -> None:
    assert set(insecure_defaults(Settings())) == {"jwt_secret", "service_token", "ml_service_token"}
    strong = Settings(jwt_secret="x" * 40, service_token="s" * 40, ml_service_token="m" * 40)
    assert insecure_defaults(strong) == []
