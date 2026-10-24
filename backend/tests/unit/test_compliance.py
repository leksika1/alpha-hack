"""Compliance rules (ADR-037): what is blocked and, as important, ordinary business news that must pass."""

import pytest

from app.domain.compliance import (
    NEWS_RULES,
    OUTPUT_RULES,
    RegistryEntry,
    check_text,
    registry_match,
    strings_of,
)


@pytest.mark.parametrize(
    "text",
    [
        "Аналитики обсуждают российскую агрессию и её последствия для экспорта.",
        "После вторжения России цены на удобрения выросли.",
        "Российские войска обвиняют в военных преступлениях, пишет издание.",
        "Russia's invasion of Ukraine pushed wheat prices higher.",
        "Reports of war crimes by Russian troops weighed on sentiment.",
    ],
)
def test_discrediting_the_armed_forces_is_blocked_everywhere(text: str) -> None:
    assert [f.code for f in check_text(text, NEWS_RULES)] == ["armed_forces_discredit"]
    assert "armed_forces_discredit" in {f.code for f in check_text(text)}


@pytest.mark.parametrize(
    "text",
    [
        "Необходимо ввести новые санкции против России.",
        "Депутаты призвали ужесточить санкции в отношении российских металлургов.",
        "We urge partners to impose sanctions on Russia.",
    ],
)
def test_calls_for_sanctions_are_blocked_in_the_services_own_words(text: str) -> None:
    assert "sanctions_call" in {f.code for f in check_text(text, OUTPUT_RULES)}


@pytest.mark.parametrize(
    "text",
    [
        "ЕС ввёл новые санкции против российских производителей стального проката.",
        "Брюссель может ввести санкции против России в декабре: экспортёрам стоит пересмотреть контракты.",
        "Минобороны заключило контракты на поставку продовольствия; выручка поставщиков выросла.",
        "Банк России сохранил ключевую ставку на уровне 16%.",
        "Агрессивная политика конкурентов давит на маржу ритейла.",
        "ВС РФ продолжают специальную военную операцию; спрос на грузоперевозки на юге вырос.",
        "Генпрокуратура возбудила дело о призывах к терроризму в соцсетях.",
        "Торговая война США и Китая ударила по ценам на сталь.",
    ],
)
def test_ordinary_business_news_passes(text: str) -> None:
    assert check_text(text, NEWS_RULES) == []


def test_news_reporting_others_calls_is_not_blocked_but_our_insight_would_be() -> None:
    news = "Глава МИД Литвы призвал ввести санкции против российских банков."
    assert check_text(news, NEWS_RULES) == []
    assert [f.code for f in check_text(news, OUTPUT_RULES)] == ["sanctions_call"]


def test_extremism_calls_in_output() -> None:
    assert "extremism_call" in {f.code for f in check_text("Это призыв к свержению власти.")}


def test_finding_carries_the_law_and_the_sentence() -> None:
    finding = check_text("Первое предложение. После вторжения России цены выросли. Третье.")[0]
    assert finding.fragment == "После вторжения России цены выросли." and "20.3.3 КоАП" in finding.law


def test_strings_of_walks_nested_payloads() -> None:
    payload = {"title": "a", "blocks": [{"text": "b", "n": 1}, ["c", {"d": "e"}]], "ok": True}
    assert strings_of(payload) == ["a", "b", "c", "e"]


def test_registry_matches_domain_and_subdomains() -> None:
    registry = [RegistryEntry("meduza.io", "Медуза", "undesirable")]
    assert registry_match("https://meduza.io/news/1", registry) is not None
    assert registry_match("https://www.meduza.io/x", registry) is not None
    assert registry_match("https://en.meduza.io/x", registry) is not None
    assert registry_match("https://notmeduza.io/x", registry) is None
