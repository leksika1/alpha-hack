"""Feed quality fixes from the dev stand (ADR-040): the key rate of a decision, one event per Bank of Russia
rate story, no live blogs. Texts are the headlines seen on the stand."""

import pytest

from app.domain.clustering import best_event, event_similarity, signature, tag_values
from app.domain.tagging import is_liveblog, tag_news
from app.services.news_store import merge_numbers


def _rate(title: str, body: str = "") -> float | None:
    numbers = tag_news(title, body).numbers
    return next((float(str(n["value"])) for n in numbers if n["kind"] == "key_rate"), None)


def test_forecast_of_the_average_rate_is_not_the_decision() -> None:
    title = "ЦБ придется дольше сохранять умеренно жесткую денежно-кредитную политику"
    body = "Прогноз средней ключевой ставки на 2027 год повышен до 12,5-13,5%."
    assert _rate(title, body) is None


@pytest.mark.parametrize(
    ("title", "rate"),
    [
        ("Банк России сохранил ключевую ставку на уровне 14% годовых", 14.0),
        ("Совет директоров Банка России снизил ключевую ставку до 16,5%", 16.5),
        ("Bank of Russia keeps key rate unchanged at 16%", 16.0),
    ],
)
def test_decision_rate_is_read(title: str, rate: float) -> None:
    assert _rate(title) == rate


def test_rate_after_a_forecast_sentence_is_still_found() -> None:
    body = "Аналитики ждали снижения. Банк России сохранил ключевую ставку на уровне 14% годовых."
    assert _rate("Решение ЦБ по ставке", body) == 14.0


def test_foreign_central_banks_are_not_the_bank_of_russia() -> None:
    fed = tag_news("ФРС снизила ставку до 4,25%", "Регулятор смягчил денежно-кредитную политику.")
    assert fed.event_type == "monetary_policy" and "cbr_key_rate_decision" not in fed.urgency_reasons
    assert fed.subjects == []


CBR_STORY = [
    ("Банк России сохранил ключевую ставку на уровне 14% годовых", "Совет директоров принял решение."),
    ("Минфин связал повышенную ключевую ставку с политикой ЦБ по инфляции", "Ведомство прокомментировало."),
    ("Набиуллина: ЦБ предметно обсудил сохранение ключевой ставки", "Глава регулятора рассказала."),
    ("Шохин: бизнес не ожидает снижения ключевой ставки ЦБ", "Глава РСПП прокомментировал решение."),
    ("Важен ли сейчас российскому бизнесу уровень ключевой ставки ЦБ?", "Опрос предпринимателей."),
]


def _sig(title: str, body: str):  # type: ignore[no-untyped-def]
    tags = tag_news(title, body)
    return signature(title, body, tags.event_type, tags.direction, tag_values(tags.as_dict()))


def test_reports_and_comments_on_one_cbr_decision_are_one_event() -> None:
    sigs = [_sig(t, b) for t, b in CBR_STORY]
    events = [("decision", sigs[0])]
    for sig in sigs[1:]:
        event_id, score = best_event(sig, events)
        assert event_id == "decision" and score == 1.0


def test_cbr_story_does_not_absorb_other_news() -> None:
    decision = _sig(*CBR_STORY[0])
    steel = _sig("Цены на арматуру выросли на 8%", "Металлурги подняли цены на прокат.")
    fed = _sig("ФРС снизила ставку до 4,25%", "Регулятор смягчил политику.")
    assert event_similarity(decision, steel) < 0.5 and event_similarity(decision, fed) < 1.0


@pytest.mark.parametrize(
    ("title", "live"),
    [
        ("Военная операция на Украине. Онлайн", True),
        ("Курс рубля: онлайн-трансляция торгов", True),
        ("Главное за день: рынки, ставка, сырьё", True),
        ("Live updates: Fed holds rates", True),
        ("Продажи онлайн выросли на 20%", False),
        ("Онлайн-ритейл ускорил рост во втором квартале", False),
        ("Ozon запустил онлайн-кредитование для продавцов", False),
    ],
)
def test_live_blogs(title: str, live: bool) -> None:
    assert is_liveblog(title) is live
    assert ("liveblog" in tag_news(title, "Текст.").flags) is live


def test_event_keeps_the_decision_rate_over_a_generic_percent() -> None:
    percent = [{"kind": "percent", "value": 13.5, "unit": "%"}]
    rate = [{"kind": "key_rate", "value": 14.0, "unit": "%"}]
    assert merge_numbers(percent, rate) == rate
    assert merge_numbers(rate, percent) == rate
    assert merge_numbers([], percent) == percent
