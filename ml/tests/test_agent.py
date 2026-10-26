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
    assert ("Gemma 4 26B A4B", "generation") in names and ("Gemma 4 26B A4B", "planning") in names
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
    fx = {"event_type": "fx", "title": "Курс евро вырос к рублю", "tags": {"currencies": ["EUR"]}}
    assert "fx_country" in compute_exposure(feats, fx, "U_1").items


def test_ocr_table_rows_are_not_bank_positions() -> None:
    from alfa_ml.agent.rules import _ocr_noise

    assert _ocr_noise("TOT -1,5% факт -2,0% 2027 -1,1% 3,4 YoY 12,0")
    assert not _ocr_noise(
        "Если раньше мы ориентировались на ставку 13,5% на конец 2026 г., то теперь ожидаем её сохранения на 14,0%."
    )


async def test_strong_ruble_is_a_risk_for_exporter(backend, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tests import fakes

    ev = {
        **fakes.RATE_EVENT,
        "event_id": "4b6f2c0e-3d7a-4c8e-9a51-6f1d2e3a4b05",
        "title": "Рубль вырос и обновил максимум с начала августа в паре с юанем",
        "summary": "Курс юаня опустился до 12,06 рубля.",
        "event_type": "fx",
        "direction": "up",
        "tags": {**fakes.RATE_EVENT["tags"], "currencies": ["CNY"], "industry_tags": [], "commodities": []},
    }
    monkeypatch.setitem(fakes.EVENTS, ev["event_id"], ev)
    orig = fakes.context_for

    def exporter_ctx(cid: str) -> dict[str, Any]:
        ctx = orig(cid)
        ctx["features"]["sensitivities.export_oriented"] = {"value": True, "source": "derived"}
        return ctx

    monkeypatch.setattr(fakes, "context_for", exporter_ctx)
    res = await Agent(generate_request(ev["event_id"], allow_llm=False)).run()
    assert res.status == "completed", res.error
    assert "выручка от экспорта снизится" in res.insight["title"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("title", "summary", "etype", "tags", "absent"),
    [
        # a foreign courier robbery tagged as monetary policy ("Fed" in "FedEx") is not the key rate
        (
            "Nearly $1M in Pokémon Cards STOLEN from FedEx",
            "A FedEx truck was robbed in California.",
            "monetary_policy",
            {},
            "rate",
        ),
        (
            "Центробанк Турции снизил ключевую ставку до 36%",
            "Центробанк Турции снизил ставку.",
            "monetary_policy",
            {},
            "rate",
        ),
        # "медики" is not copper, a film release ("прокат") is not rolled steel
        (
            "ВСУ обстреляли скорые с тяжелыми больными",
            "Медики больницы не смогли вывезти пациентов.",
            "industry",
            {"commodities": ["nonferrous"]},
            "commodity",
        ),
        (
            "Три премьеры начала ноября: что посмотреть в кино",
            "В начале ноября в прокат выходят драма и мелодрама.",
            "industry",
            {"industry_tags": ["metals", "steel"], "commodities": ["steel"]},
            "industry",
        ),
        # a sanctions headline with no Russian trade or payments in it
        (
            "Минюст объявил иноагентом журналиста",
            "Минюст объявил иноагентами журналиста и блогера.",
            "sanctions",
            {},
            "fx_country",
        ),
        # the company's trade country is mentioned, but not trade
        (
            "Посол США в Турции заявил о возможном ударе по Ирану",
            "Посол заявил о возможном ударе.",
            "other",
            {"countries": ["ТУРЦИЯ", "ИРАН"]},
            "fx_country",
        ),
    ],
)
def test_live_feed_false_positives_are_screened(title, summary, etype, tags, absent) -> None:  # type: ignore[no-untyped-def]
    from alfa_ml.profile import compute_exposure, normalize_event

    feats = {
        "products.loan": {"value": True, "source": "csv"},
        "sensitivities.rate_sensitive": {"value": True, "source": "derived"},
        "sensitivities.fx_sensitive": {"value": True, "source": "derived"},
        "sensitivities.commodity_exposure": {"value": ["steel", "nonferrous"], "source": "derived"},
        "industry_tags": {"value": ["metals", "wholesale"], "source": "csv"},
        "fts_countries": {"value": ["ТУРЦИЯ", "КИТАЙ"], "source": "csv"},
    }
    ev = normalize_event({"event_type": etype, "title": title, "summary": summary, "tags": tags})
    assert absent not in compute_exposure(feats, ev, "U_1").items


def test_real_business_news_keeps_its_channels() -> None:
    from alfa_ml.profile import compute_exposure, normalize_event

    feats = {
        "products.loan": {"value": True, "source": "csv"},
        "sensitivities.commodity_exposure": {"value": ["steel"], "source": "derived"},
        "industry_tags": {"value": ["metals"], "source": "csv"},
    }
    rate = normalize_event(
        {
            "event_type": "monetary_policy",
            "title": "Банк России сохранил ключевую ставку на уровне 14%",
            "tags": {},
        }
    )
    assert "rate" in compute_exposure(feats, rate, "U_1").items
    steel = normalize_event(
        {
            "event_type": "commodities",
            "title": "Цены на арматуру и сортовой прокат выросли на 8%",
            "summary": "",
            "tags": {"commodities": ["steel"], "industry_tags": ["metals"]},
        }
    )
    items = compute_exposure(feats, steel, "U_1").items
    assert "commodity" in items and "industry" in items


async def test_failing_provider_opens_the_circuit_for_next_runs(backend, llm_script: LLMScript) -> None:  # type: ignore[no-untyped-def]
    from alfa_ml import llm as llm_mod

    llm_script.status = 503
    for _ in range(2):
        res = await Agent(generate_request(RATE_EVENT["event_id"])).run()
        assert res.status == "completed"  # rules@1 answers while the provider fails
    assert llm_mod.breaker_open()
    calls = len(llm_script.calls)
    res = await Agent(generate_request(STEEL_EVENT["event_id"])).run()
    assert res.status == "completed" and len(llm_script.calls) == calls  # no waiting on a dead provider
    llm_mod.breaker_reset()
    llm_script.status = 200
    assert not llm_mod.breaker_open()


async def test_official_rate_is_cited_and_checked(backend, backend_validator) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(RATE_EVENT["event_id"], allow_llm=False)).run()
    ins = res.insight
    assert ins is not None
    sm = [s for s in ins["sources"] if s["id"] == "sm"]
    assert sm and sm[0]["type"] == "external_stat" and sm[0]["publisher"] == "Банк России"
    assert any("sm" in s["source_refs"] and "14%" in s["text"] for s in ins["statements"])
    assert not any("официальное значение" in s["text"] for s in ins["statements"])  # 14% agrees
    assert backend_validator(ins, context_for("U_110108")["features"], CHUNKS, PRODUCT_IDS) == []


def test_news_figure_against_the_official_one() -> None:
    from alfa_ml.agent import market
    from tests.fakes import MARKET

    off = market.official(MARKET["key_rate"], "2026-10-21T10:00:00+00:00")
    assert off is not None and off.value == 14.0 and off.prev_value == 14.5 and off.since == "2026-09-12"
    assert market.check("ЦБ сохранил ключевую ставку 14% годовых", off)["mismatch"] is False  # type: ignore[index]
    assert market.check("Банк России повысил ключевую ставку до 16%", off)["mismatch"] is True  # type: ignore[index]
    assert market.check("Выручка выросла на 16%", off) is None  # not a rate figure
    usd = market.official(MARKET["usd_rub"])
    assert usd is not None and market.check("Курс доллара достиг 95 рублей", usd)["mismatch"] is True  # type: ignore[index]
