import json
from collections.abc import AsyncIterator
from typing import Any

from app.domain.chat_validator import check_chat_final
from app.domain.contracts import CONTRACTS_DIR
from app.services.feed import priority_reasons
from app.services.qa import _parse_sse


def example_final() -> dict[str, Any]:
    lines = (CONTRACTS_DIR / "examples" / "chat.sse.txt").read_text(encoding="utf-8").splitlines()
    done = next(i for i, line in enumerate(lines) if line == "event: done")
    final: dict[str, Any] = json.loads(lines[done + 1].removeprefix("data: "))
    return final


def test_contract_example_answer_passes() -> None:
    check = check_chat_final(example_final())
    assert check.ok and not check.fixes


def test_missing_disclaimer_is_added_by_the_backend() -> None:
    final = example_final()
    final["disclaimers"] = []
    check = check_chat_final(final)
    assert check.ok and check.fixes == ["added not_investment_advice disclaimer"]
    assert check.final["disclaimers"][0]["code"] == "not_investment_advice"
    assert final["disclaimers"] == []  # the agent's object is not mutated


def test_fact_without_valid_source_is_rejected() -> None:
    final = example_final()
    final["statements"][0]["source_refs"] = ["s9"]
    check = check_chat_final(final)
    assert not check.ok and check.problems[0]["code"] == "unsupported-statement"
    final["statements"][0]["kind"] = "model_inference"
    assert check_chat_final(final).ok  # an inference may stand without a source


def test_schema_errors_are_reported() -> None:
    check = check_chat_final({"answer": "x", "sources": []})
    assert not check.ok and check.problems[0]["code"] == "schema"


async def test_sse_parser_handles_events_multiline_and_trailing_data() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in [
            "event: step",
            'data: {"name": "a"}',
            "",
            "data: 1",
            "",
            "event: token",
            'data: {"delta":',
            'data: "x"}',
        ]:
            yield line

    events = [e async for e in _parse_sse(lines())]
    assert events == [("step", {"name": "a"}), ("message", 1), ("token", {"delta": "x"})]


def test_priority_reasons_are_readable_and_ordered() -> None:
    reasons = priority_reasons({"recency": 0.5, "industry": 0.2, "rate": 0.3, "region": 0.01, "x_new": 0.1})
    assert reasons == ["ставка и ваши кредитные продукты", "ваша отрасль"]
    assert priority_reasons({"recency": 0.4}) == []
