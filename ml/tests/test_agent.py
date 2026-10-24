import json
from typing import Any

import pytest

from alfa_ml.agent.runner import Agent
from tests.fakes import (
    ANALYTICS,
    PRODUCTS,
    RATE_EVENT,
    SPORT_EVENT,
    STEEL_CHUNK,
    STEEL_EVENT,
    STEEL_IMPORT_DOWN_EVENT,
    LLMScript,
    context_for,
    generate_request,
)

CHUNKS = {ANALYTICS[0]["chunk_id"], STEEL_CHUNK["chunk_id"]}
PRODUCT_IDS = {p["product_id"] for p in PRODUCTS["products"]}


def analyst_answer(body: dict[str, Any]) -> dict[str, Any]:
    """A plausible analyst answer that also tries a few things the assembler must neutralise."""
    return {
        "relevant": True,
        "title": "Пауза ЦБ на 14%: кредит и лизинг компании не подешевеют до конца года",
        "summary": "Ставка сохранена на 14%. Для компании с кредитом и лизингом стоимость долга останется высокой; планы рефинансирования стоит сдвинуть.",
        "polarity": "risk",
        "event_summary": "Банк России сохранил ключевую ставку 14% годовых.",
        "event_type": "monetary_policy",
        "participants": [{"name": "Банк России", "type": "regulator"}],
        "key_facts": [{"text": "Ключевая ставка сохранена на уровне 14%.", "sources": ["s1"]}],
        "claims": [
            {
                "id": "c1",
                "text": "Ставка сохранена на уровне 14%.",
                "kind": "fact",
                "confidence": 0.95,
                "sources": ["s1"],
            },
            {
                "id": "c2",
                "text": "У компании кредит и лизинг в Альфа-Банке.",
                "kind": "fact",
                "confidence": 1,
                "sources": ["sc"],
                "features": ["products.loan", "products.leasing"],
            },
            {
                "id": "c3",
                "text": "Альфа-Банк ожидает сохранения ставки до конца года.",
                "kind": "bank_analytics",
                "confidence": 0.9,
                "sources": ["sa1"],
            },
            {
                "id": "c4",
                "text": "Банк считает, что рубль укрепится на 20%.",
                "kind": "bank_analytics",
                "confidence": 0.9,
                "sources": [],
            },
            {
                "id": "c5",
                "text": "Процентные расходы не снизятся в ближайшие кварталы.",
                "kind": "model_inference",
                "confidence": 0.8,
                "sources": ["s1", "sa1"],
                "features": ["sensitivities.rate_sensitive", "made.up.feature"],
            },
        ],
        "why_it_matters": [
            {
                "text": "Кредит и лизинг компании — процентные платежи зависят от ставки.",
                "features": ["products.loan", "products.leasing", "nonexistent.path"],
                "claims": ["c2", "c5"],
            }
        ],
        "risks": [
            {
                "text": "Процентная нагрузка не снизится 1–2 квартала.",
                "horizon": "quarters",
                "drivers": ["решение ЦБ"],
                "claims": ["c5"],
            }
        ],
        "opportunities": [],
        "scenarios": [
            {
                "name": "Пауза до конца года",
                "logic": "ЦБ держит ставку из-за инфляции.",
                "plausibility": "high",
                "signals": ["инфляция"],
                "claims": ["c3"],
            }
        ],
        "bank_positions": [
            {
                "source": "sa1",
                "text": "Аналитики ждут сохранения ставки 14% до конца года.",
                "quote": "мы ожидаем ее сохранения на текущем значении 14,0% до конца года",
            },
            {"source": "sa9", "text": "Несуществующая позиция банка.", "quote": "выдумано"},
        ],
        "model_disagrees_with_bank": False,
        "impact_level": "high",
        "impact_rationale": "Прямая подверженность через кредит и лизинг.",
        "actions": [
            {
                "type": "contact_manager",
                "text": "Обсудить фиксацию ставки по кредиту.",
                "product_id": "loan_refinancing",
            },
            {"type": "application", "text": "Оформить выдуманный продукт.", "product_id": "magic_product"},
        ],
    }


@pytest.mark.parametrize("event", [RATE_EVENT, STEEL_EVENT])
@pytest.mark.parametrize("role", ["cfo", "ceo", "chief_accountant", "treasurer", "owner"])
async def test_rules_insight_passes_backend_validator(backend, backend_validator, event, role) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(event["event_id"], role=role, allow_llm=False)).run()
    assert res.status == "completed", res.error
    insight = res.insight
    assert insight is not None
    problems = backend_validator(insight, context_for("U_110108")["features"], CHUNKS, PRODUCT_IDS)
    assert problems == []
    assert insight["role_variant"] == role
    assert {"name": "rules@1", "purpose": "template"} in insight["models_used"]
    assert insight["relevance"]["scorer"] == "ml-ranker@1"
    assert insight["why_it_matters"] and all(r["feature_refs"] for r in insight["why_it_matters"])
    assert any(d["code"] == "not_investment_advice" for d in insight["disclaimers"])
    kinds = {s["kind"] for s in insight["statements"]}
    assert "fact" in kinds and "bank_analytics" in kinds


async def test_rate_insight_has_quantitative_estimate(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(RATE_EVENT["event_id"], allow_llm=False)).run()
    quant = {q["metric"]: q for q in res.insight["impact"]["quantitative"]}  # type: ignore[index]
    assert quant["interest_cost_delta_per_100m"]["value"] == 500000  # 50 б.п. × 100 млн ₽
    assert (
        "неизвестн" in quant["interest_cost_delta_per_100m"]["basis"]
        or "отсутствует" in quant["interest_cost_delta_per_100m"]["basis"]
    )


async def test_second_order_counterparty_named(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(STEEL_EVENT["event_id"], role="ceo", allow_llm=False)).run()
    text = json.dumps(res.insight, ensure_ascii=False)
    assert "U_200001" in text  # the steel supplier with 31% of outgoing payments
    assert any(s["kind"] == "hypothesis" and "поставщик" in s["text"] for s in res.insight["statements"])  # type: ignore[index]


async def test_irrelevant_event_is_skipped(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(SPORT_EVENT["event_id"], allow_llm=False)).run()
    assert res.status == "skipped" and res.skip_reason == "not_relevant"


async def test_cold_profile_skips_rate_event(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(RATE_EVENT["event_id"], company_id="U_999999", allow_llm=False)).run()
    assert res.status == "skipped"


async def test_llm_draft_is_sanitised(backend, backend_validator, llm_script: LLMScript) -> None:  # type: ignore[no-untyped-def]
    llm_script.handlers = {
        "planning": {
            "analytics_queries": ["прогноз ставки 2027"],
            "need_counterparties": False,
            "focus": "стоимость долга",
        },
        "generation": analyst_answer,
        "self_check": {"verdict": "pass", "issues": []},
    }
    res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
    assert res.status == "completed", res.error
    ins = res.insight
    assert ins is not None
    assert backend_validator(ins, context_for("U_110108")["features"], CHUNKS, PRODUCT_IDS) == []
    assert ins["title"].startswith("Пауза ЦБ")
    by_text = {s["text"]: s for s in ins["statements"]}
    assert (
        by_text["Банк считает, что рубль укрепится на 20%."]["kind"] == "model_inference"
    )  # no analytics source
    assert all(p["source_ref"] == "sa1" for p in ins["bank_view"]["positions"])
    assert ins["bank_view"]["positions"][0]["quote"]  # verified against the chunk text
    all_features = {f for r in ins["why_it_matters"] for f in r["feature_refs"]} | {
        f for s in ins["statements"] for f in s["feature_refs"]
    }
    assert "nonexistent.path" not in all_features and "made.up.feature" not in all_features
    assert all(a["product_id"] in (None, "loan_refinancing") for a in ins["actions"])
    names = {(m["name"], m["purpose"]) for m in ins["models_used"]}
    assert ("Qwen3.6 35B A3B", "generation") in names and ("Gemma 4 26B A4B", "planning") in names
    assert [c["purpose"] for c in llm_script.calls] == ["planning", "generation", "self_check"]
    assert ins["impact"]["quantitative"]  # numbers come from the deterministic calculation


async def test_critic_revision_round(backend, llm_script: LLMScript, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ML_MAX_ITERATIONS", "2")
    from alfa_ml.config import get_settings

    get_settings.cache_clear()
    verdicts = iter(
        [
            {
                "verdict": "revise",
                "issues": [{"field": "summary", "problem": "пересказ", "fix": "объясни влияние"}],
                "need_more_data": {"analytics_queries": ["инфляция прогноз"]},
            },
            {"verdict": "pass"},
        ]
    )
    llm_script.handlers = {
        "planning": {},
        "generation": analyst_answer,
        "self_check": lambda body: next(verdicts),
    }
    res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
    assert res.status == "completed"
    purposes = [c["purpose"] for c in llm_script.calls]
    assert purposes.count("generation") == 2 and purposes.count("self_check") == 2
    names = [t["name"] for t in res.trace]
    assert "critic_more_data" in names and "revise_1" in names


async def test_non_json_llm_falls_back_to_rules(backend, backend_validator, llm_script: LLMScript) -> None:  # type: ignore[no-untyped-def]
    # backend LLM_PROVIDER=fake answers with plain text — the agent must not loop or fail
    res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
    assert res.status == "completed"
    assert {"name": "rules@1", "purpose": "template"} in res.insight["models_used"]  # type: ignore[index]
    assert len(llm_script.calls) <= 4
    assert backend_validator(res.insight, context_for("U_110108")["features"], CHUNKS, PRODUCT_IDS) == []


async def test_budget_exhausted_degrades_without_more_calls(backend, llm_script: LLMScript) -> None:  # type: ignore[no-untyped-def]
    llm_script.status = 429
    res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
    assert res.status == "completed"
    assert len(llm_script.calls) == 1


async def test_prompt_injection_not_sent_to_llm(backend, llm_script: LLMScript) -> None:  # type: ignore[no-untyped-def]
    llm_script.handlers = {"planning": {}, "self_check": {"verdict": "pass"}}
    await Agent(generate_request(STEEL_EVENT["event_id"])).run()
    sent = json.dumps([c["body"]["messages"] for c in llm_script.calls], ensure_ascii=False)
    assert "Игнорируй предыдущие инструкции" not in sent
    assert "<untrusted_content" in sent


async def test_trace_steps_written(backend) -> None:  # type: ignore[no-untyped-def]
    await Agent(generate_request(RATE_EVENT["event_id"], allow_llm=False)).run()
    types = [s["step_type"] for s in backend]
    assert types[0] == "plan" and types[-1] == "final"
    assert "self_check" in types and "retrieval" in types


async def test_budget_guard_uses_rules_for_background_events(
    backend, llm_script: LLMScript, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ML_LLM_MIN_SCORE", "0.99")
    from alfa_ml.config import get_settings

    get_settings.cache_clear()
    res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
    assert res.status == "completed" and llm_script.calls == []
    assert any(t["name"] == "llm_budget_guard" for t in res.trace)


def test_market_side_seller_vs_buyer() -> None:
    from alfa_ml.agent.rules import market_side

    assert market_side("46.72", ["steel"]) == "seller"  # metal wholesale
    assert market_side("24.10", ["steel"]) == "seller"  # steel mill
    assert market_side("41.20", ["steel", "cement"]) == "buyer"  # developer
    assert market_side("01.11", ["fertilizers"]) == "buyer"  # farm buys fertilizers
    assert market_side("20.15", ["fertilizers"]) == "seller"
    assert market_side("41.20", []) == "none"
    assert market_side("46.3", ["steel"]) == "buyer"  # food wholesale is not a metal trader


async def test_steel_price_up_is_cost_for_developer(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(STEEL_EVENT["event_id"], company_id="U_410001", allow_llm=False)).run()
    assert res.status == "completed", res.error
    title = res.insight["title"]  # type: ignore[index]
    assert "закупки компании подорожают" in title
    assert "поддержит выручку" not in title


async def test_steel_price_up_is_opportunity_for_metal_trader(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(STEEL_EVENT["event_id"], allow_llm=False)).run()
    assert "поддержит выручку" in res.insight["title"]  # type: ignore[index]


async def test_cheap_import_is_pressure_for_seller_and_saving_for_buyer(backend, backend_validator) -> None:  # type: ignore[no-untyped-def]
    seller = await Agent(generate_request(STEEL_IMPORT_DOWN_EVENT["event_id"], allow_llm=False)).run()
    assert seller.status == "completed", seller.error
    assert "давит на цены" in seller.insight["title"]  # type: ignore[index]
    assert "подорожа" not in seller.insight["title"]  # type: ignore[index]
    buyer = await Agent(
        generate_request(STEEL_IMPORT_DOWN_EVENT["event_id"], company_id="U_410001", allow_llm=False)
    ).run()
    assert buyer.status == "completed", buyer.error
    assert "подешеве" in buyer.insight["title"]  # type: ignore[index]
    for r in (seller, buyer):
        problems = backend_validator(
            r.insight, context_for(r.insight["company_id"])["features"], CHUNKS, PRODUCT_IDS
        )  # type: ignore[index]
        assert problems == []


def test_normalize_event_fx_direction_and_mortgage_tags() -> None:
    from alfa_ml.profile import normalize_event

    ev = {"event_type": "fx", "direction": "up", "title": "Рубль в пятницу вырос в паре с юанем", "tags": {}}
    assert normalize_event(ev)["direction"] == "down"  # the yuan went down
    assert ev["direction"] == "up"  # the backend's event is not mutated
    weak = {"event_type": "fx", "direction": "down", "title": "Рубль ослаб к доллару", "tags": {}}
    assert normalize_event(weak)["direction"] == "up"
    yuan = {"event_type": "fx", "direction": "up", "title": "Курс юаня резко вырос", "tags": {}}
    assert normalize_event(yuan)["direction"] == "up"
    mort = {
        "event_type": "industry",
        "title": "Льготную ИТ-ипотеку могут завершить",
        "tags": {"industry_tags": ["it"]},
    }
    assert {"developer", "construction"} <= set(normalize_event(mort)["tags"]["industry_tags"])


def test_currency_match_counts_only_for_fx_stories() -> None:
    from alfa_ml.profile import compute_exposure

    feats = {
        "fts_countries": {"value": ["ИТАЛИЯ"], "source": "csv"},
        "industry_tags": {"value": ["agriculture"], "source": "csv"},
    }
    story = {
        "event_type": "industry",
        "tags": {"industry_tags": ["real_estate"], "countries": ["ГЕРМАНИЯ"], "currencies": ["EUR"]},
    }
    assert "fx_country" not in compute_exposure(feats, story, "U_1").items
    fx = {"event_type": "fx", "tags": {"currencies": ["EUR"]}}
    assert "fx_country" in compute_exposure(feats, fx, "U_1").items


def test_ocr_table_rows_are_not_bank_positions() -> None:
    from alfa_ml.agent.rules import _ocr_noise

    assert _ocr_noise("TOT -1,5% факт -2,0% 2027 -1,1% 3,4 YoY 12,0")
    assert not _ocr_noise(
        "Если раньше мы ориентировались на ставку 13,5% на конец 2026 г., то теперь ожидаем её сохранения на 14,0%."
    )
