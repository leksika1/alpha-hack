import re
from pathlib import Path

import pytest

from app.domain.okved import SECTION_NAMES, classify, load_reference, normalize_code

REF_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"
TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@pytest.fixture(scope="module")
def ref():  # type: ignore[no-untyped-def]
    return load_reference(REF_DIR)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("46.72", "46.72"), (" 01.11 ", "01.11"), ("1.11", "01.11"), ("68.20.2", "68.20.2"), ("99", "99"),
     ("", None), (None, None), ("abc", None), ("46.721.1", None)],
)  # fmt: skip
def test_normalize_code(raw: str | None, expected: str | None) -> None:
    assert normalize_code(raw) == expected


def test_reference_integrity(ref) -> None:  # type: ignore[no-untyped-def]
    assert len(ref.classes) >= 85
    for cls in ref.classes.values():
        assert re.fullmatch(r"\d{2}", cls.class_code)
        assert cls.section in SECTION_NAMES
        assert cls.industry_tags, f"class {cls.class_code} has no tags"
        assert all(TAG_RE.match(t) for t in (*cls.industry_tags, *cls.commodities))
    for ov in ref.overrides:
        assert normalize_code(ov.prefix) == ov.prefix
        assert ov.prefix[:2] in ref.classes
        assert all(TAG_RE.match(t) for t in (*ov.extra_tags, *ov.extra_commodities))


def test_metals_wholesale(ref) -> None:  # type: ignore[no-untyped-def]
    e = classify("46.72", "Торговля оптовая металлами", ref)
    assert e.section == "G" and e.class_code == "46" and e.group_code == "46.7"
    assert {"wholesale", "metals"} <= set(e.industry_tags)
    assert {"steel", "nonferrous"} <= set(e.commodities)
    assert e.flags["consumer_demand"] is False


def test_longest_prefix_overrides_flags(ref) -> None:  # type: ignore[no-untyped-def]
    food = classify("46.38", None, ref)
    assert food.flags["consumer_demand"] is True  # from 46.3
    assert food.flags["import_dependent"] is False  # 46.3 overrides class 46
    assert "fish" in food.commodities  # from 46.38
    grain = classify("46.21", None, ref)
    assert grain.flags["export_oriented"] is True
    assert {"agriculture", "grain_trade"} <= set(grain.industry_tags)


def test_prefix_does_not_leak_to_other_groups(ref) -> None:  # type: ignore[no-untyped-def]
    e = classify("46.90", None, ref)
    assert "metals" not in e.industry_tags and "food" not in e.industry_tags


def test_subclass_codes_and_section_a(ref) -> None:  # type: ignore[no-untyped-def]
    assert classify("68.20.2", None, ref).section == "L"
    agro = classify("01.11", None, ref)
    assert agro.section == "A" and "grain" in agro.commodities


def test_unknown_codes_are_flagged_not_invented(ref) -> None:  # type: ignore[no-untyped-def]
    garbage = classify("xx", "?", ref)
    assert garbage.known is False and garbage.section is None and garbage.industry_tags == []
    missing_class = classify("04.10", None, ref)
    assert missing_class.known is False and missing_class.section is None
