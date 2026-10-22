from decimal import Decimal

import pytest

from app.domain.cleaning import (
    clean_company_rows,
    clean_rc_name,
    is_foreign_ownership,
    parse_countries,
    parse_flag,
    parse_revenue,
    segment_by_revenue,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.0", True),
        ("0.0", False),
        ("1", True),
        ("0", False),
        ("", None),
        (None, None),
        (" 1.0 ", True),
        ("x", None),
    ],
)
def test_parse_flag(raw: str | None, expected: bool | None) -> None:
    assert parse_flag(raw) is expected


def test_revenue_stub_becomes_null() -> None:
    assert parse_revenue("1000.0") == (None, True)
    assert parse_revenue("1000") == (None, True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("136493.0", Decimal("136493.00")),
        ("1001.0", Decimal("1001.00")),
        ("", None),
        ("-5", None),
        ("abc", None),
    ],
)
def test_parse_revenue_values(raw: str, expected: Decimal | None) -> None:
    value, stub = parse_revenue(raw)
    assert value == expected and stub is False


def test_parse_countries_normalizes_list() -> None:
    assert parse_countries("ОАЭ, ИНДИЯ, КИТАЙ") == ["ИНДИЯ", "КИТАЙ", "ОАЭ"]
    assert parse_countries("китай,  КИТАЙ ,") == ["КИТАЙ"]
    assert parse_countries("") == [] and parse_countries(None) == []


@pytest.mark.parametrize("raw", ["", "ИНН не определён", "Не определено", "  ", None])
def test_rc_name_garbage_to_null(raw: str | None) -> None:
    assert clean_rc_name(raw) is None


def test_rc_name_kept() -> None:
    assert clean_rc_name(" УРАЛЬСКИЙ ") == "УРАЛЬСКИЙ"


def test_foreign_ownership() -> None:
    assert is_foreign_ownership("СОБСТВЕННОСТЬ ИНОСТРАННЫХ ЮРИДИЧЕСКИХ ЛИЦ") is True
    assert is_foreign_ownership("ЧАСТНАЯ СОБСТВЕННОСТЬ") is False
    assert is_foreign_ownership("") is None


def test_segments() -> None:
    assert segment_by_revenue(None) == "unknown"
    assert segment_by_revenue(Decimal("15000000001")) == "large"
    assert segment_by_revenue(Decimal("15000000000")) == "mid"
    assert segment_by_revenue(Decimal("800000000")) == "mid"
    assert segment_by_revenue(Decimal("799999999")) == "small"


def _row(**kw: str) -> dict[str, str | None]:
    base: dict[str, str | None] = {
        "synthetic_u": "U_1", "okfs_name": "ЧАСТНАЯ СОБСТВЕННОСТЬ", "eco_mark_label": "",
        "mainokved_name": "X", "negativemark": "1.0", "cl_type": "CLIENT", "cl_product_rko_mark": "1.0",
        "cl_product_loa_mark": "1.0", "cl_product_lea_mark": "0.0", "factoring_mark": "1.0",
        "fts_flag": "0.0", "fts_country_name": "",
        "revenue_company": "5000000.0", "group_id": "group_1_sb", "city": "г. Москва", "region": "Москва",
    }  # fmt: skip
    base.update(kw)
    return base


def test_duplicate_ids_become_invalid_marker_without_merging() -> None:
    c = clean_company_rows("U_428697", [_row(), _row(revenue_company="1.0"), _row()], "46.90")
    assert c.is_valid is False and c.invalid_reason == "pseudo_id_collision" and c.source_rows == 3
    assert c.revenue_rub is None and c.okved_code is None and c.products == []


def test_valid_company_cleaning() -> None:
    c = clean_company_rows("U_1", [_row()], "46.90")
    assert c.is_valid and c.revenue_rub == Decimal("5000000.00") and not c.revenue_stub
    assert c.eco_stage is None
    assert c.owner_info_reliable is True
    assert set(c.products) == {"rko", "loan", "factoring"}
    assert c.fts_flag is False and c.issues == []


def test_fts_flag_derived_from_countries_and_mismatches_reported() -> None:
    c = clean_company_rows("U_1", [_row(fts_flag="0.0", fts_country_name="КИТАЙ")], None)
    assert c.fts_flag is True and c.countries == ["КИТАЙ"] and "fts_flag_0_with_countries" in c.issues
    c2 = clean_company_rows("U_2", [_row(fts_flag="1.0", fts_country_name="")], None)
    assert c2.fts_flag is True and "fts_flag_1_without_countries" in c2.issues


def test_revenue_stub_marked() -> None:
    c = clean_company_rows("U_1", [_row(revenue_company="1000.0")], None)
    assert c.revenue_rub is None and c.revenue_stub is True
