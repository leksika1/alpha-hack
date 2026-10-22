import json
from pathlib import Path

from app.domain.clustering import (
    SAME_EVENT,
    best_event,
    event_similarity,
    is_near_duplicate,
    signature,
    stems,
    tag_values,
)
from app.domain.tagging import tag_news

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "news" / "fixture_news.json"
ITEMS = {i["id"]: i for i in json.loads(FIXTURES.read_text(encoding="utf-8"))["items"]}


def sig(title: str, body: str):  # type: ignore[no-untyped-def]
    t = tag_news(title, body)
    return signature(title, body, t.event_type, t.direction, tag_values(t.as_dict()))


def fx(item_id: str):  # type: ignore[no-untyped-def]
    return sig(ITEMS[item_id]["title"], ITEMS[item_id]["body"])


def test_stems_ignore_stopwords_and_endings() -> None:
    assert stems("Банк России сохранил ключевую ставку") == stems("банка россии сохранила ключевой ставки")
    assert "и" not in stems("сталь и медь")


def test_two_reports_of_the_rate_decision_are_one_event() -> None:
    assert event_similarity(fx("fx-001"), fx("fx-002")) >= SAME_EVENT


def test_opposite_directions_never_merge() -> None:
    assert event_similarity(fx("fx-003"), fx("fx-015")) == 0.0  # steel up vs steel down


def test_generic_percent_is_not_evidence() -> None:
    steel = sig("Цены на сталь выросли на 8%", "Металлурги подняли цены на прокат на 8%.")
    copper = sig("Цены на медь выросли на 8%", "Производители меди подняли цены на 8%.")
    assert event_similarity(steel, copper) < SAME_EVENT


def test_unrelated_fixture_events_stay_separate() -> None:
    ids = ["fx-003", "fx-004", "fx-005", "fx-007", "fx-009", "fx-010", "fx-011", "fx-012"]
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            assert event_similarity(fx(a), fx(b)) < SAME_EVENT, (a, b)


def test_near_duplicate_republication() -> None:
    a = sig(
        "Цены на арматуру выросли на 8% за неделю",
        "Металлургические комбинаты Урала повысили отпускные цены.",
    )
    b = sig("Цены на арматуру выросли на 8% за неделю!", "Металлургические комбинаты Урала повысили цены.")
    assert is_near_duplicate(a, b)
    assert not is_near_duplicate(a, fx("fx-013"))


def test_best_event_picks_the_most_similar_above_threshold() -> None:
    events = [("rate", fx("fx-001")), ("steel", fx("fx-003")), ("yuan", fx("fx-010"))]
    assert best_event(fx("fx-002"), events)[0] == "rate"
    assert best_event(fx("fx-016"), events)[0] is None
