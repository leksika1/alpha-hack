"""One spelling of Russian regions for the client data and the news tagger (ADR-045)."""

import pytest

from app.domain.regions import REGION_STEMS, normalize_city, normalize_region
from app.domain.tagging import tag_news


def test_all_federal_subjects_are_known() -> None:
    assert len(REGION_STEMS) == 89


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Свердловская обл.", "Свердловская область"),
        ("СВЕРДЛОВСКАЯ ОБЛАСТЬ", "Свердловская область"),
        ("Московская обл", "Московская область"),
        ("г. Москва", "Москва"),
        ("Москва", "Москва"),
        ("Респ. Татарстан", "Республика Татарстан"),
        ("Татарстан Респ", "Республика Татарстан"),
        ("Ханты-Мансийский автономный округ - Югра", "Ханты-Мансийский автономный округ — Югра"),
        ("Ямало-Ненецкий АО", "Ямало-Ненецкий автономный округ"),
        ("Республика Саха (Якутия)", "Республика Саха (Якутия)"),
        ("Кировская обл.", "Кировская область"),
        ("Неизвестный край", "Неизвестный край"),
        ("  ", None),
        (None, None),
    ],
)
def test_region_spellings_become_the_official_name(raw: str | None, expected: str | None) -> None:
    assert normalize_region(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("г. Москва", "Москва"), ("Г  Казань", "Казань"), ("город Тула", "Тула"), ('"Мск "Юг"', "Мск Юг"),
     ("Екатеринбург", "Екатеринбург"), ("", None)],
)  # fmt: skip
def test_city_prefixes_are_dropped(raw: str, expected: str | None) -> None:
    assert normalize_city(raw) == expected


def _regions(title: str) -> list[str]:
    return tag_news(title, "").regions


def test_tagger_finds_regions_by_their_cities() -> None:
    assert _regions("В Нижнем Новгороде открылся логистический центр") == ["Нижегородская область"]
    assert _regions("Губернатор Кузбасса обсудил добычу угля") == ["Кемеровская область"]
    assert _regions("Завод в Тольятти остановил конвейер") == ["Самарская область"]
    assert _regions("Порт Новороссийска нарастил перевалку") == ["Краснодарский край"]
    assert "Ханты-Мансийский автономный округ — Югра" in _regions("Нефтяники Сургута увеличили добычу")


def test_tagger_ignores_surnames_and_common_words() -> None:
    assert _regions("Иванов и Киров обсудили курганы и находку археологов") == []
    assert _regions("Грозный тон переговоров о поставках") == []
