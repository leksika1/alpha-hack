import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from app.domain.company_context import CompanyFacts, Counterparty, DirectionStats, build_context

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "examples"
    / "tools"
    / "get_company_context.response.json"
)


def facts(**kw: object) -> CompanyFacts:
    base = CompanyFacts(
        company_id="U_1",
        is_valid=True,
        client_type="CLIENT",
        okved_code="46.72",
        okved_name="Торговля оптовая металлами",
        okved_section="G",
        okved_section_name="Торговля",
        industry_tags=("wholesale", "trade", "metals"),
        okved_commodities=("steel", "nonferrous"),
        okved_flags={"import_dependent": True, "consumer_demand": False, "export_oriented": False,
                     "rate_sensitive_industry": True},
        region="Свердловская область",
        city="г. Екатеринбург",
        age_bucket="4-7",
        staff_bucket="16-100",
        eco_stage="Оптимизация",
        okfs_name="ЧАСТНАЯ СОБСТВЕННОСТЬ",
        foreign_ownership=False,
        owner_info_reliable=True,
        revenue_rub=Decimal("1104149000"),
        revenue_stub=False,
        group_id="group_1_sb",
        group_size=9,
        group_revenue_rub=Decimal("5320000000"),
        group_client_count=8,
        products=("loan", "payroll", "rko"),
        fts_flag=True,
        fts_countries=("КИТАЙ",),
        incoming=DirectionStats(
            total_rub=1000.0, known_rub=600.0, n_known=3, n_unknown=2, hhi=2500.0,
            top=(Counterparty("U_2", 400.0, 10, 0.4, True, "41.20", "Стр", ("construction",)),),
            industry_shares={"construction": 0.28, "real_estate": 0.02},
        ),
        outgoing=DirectionStats(total_rub=500.0, known_rub=500.0, n_known=2, hhi=5000.0,
                                industry_shares={"metals": 0.7}),
        shared_person_companies=("U_9", "U_8"),
        user_provided={"currencies": ["CNY"]},
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def test_features_have_provenance() -> None:
    res = build_context(facts())
    for path, feat in res.features.items():
        assert feat["source"] in {"csv", "derived", "user", "external"}, path
        assert "value" in feat, path


def test_rate_and_fx_sensitivity_from_products_and_fts() -> None:
    res = build_context(facts())
    assert res.features["sensitivities.rate_sensitive"]["value"] is True
    assert "loan" in res.features["sensitivities.rate_sensitive"]["note"]
    assert res.features["sensitivities.fx_sensitive"]["value"] is True
    assert res.features["sensitivities.import_dependent"]["value"] is True
    assert res.index["rate_sensitive"] and res.index["fx_sensitive"]


def test_user_credit_plans_make_company_rate_sensitive() -> None:
    res = build_context(facts(products=("rko",), user_provided={"credit_plans": "кредит на 300 млн в 2027"}))
    feat = res.features["sensitivities.rate_sensitive"]
    assert feat["value"] is True and feat["source"] == "user"
    assert res.features["user_provided.credit_plans"]["source"] == "user"
    assert res.completeness["user_fields"] == ["credit_plans"]


def test_fx_from_user_currencies_ignores_rub() -> None:
    no_fts = {"fts_flag": False, "fts_countries": ()}
    assert (
        build_context(facts(**no_fts, user_provided={"currencies": ["RUB"]})).index["fx_sensitive"] is False
    )
    assert build_context(facts(**no_fts, user_provided={"currencies": ["usd"]})).index["fx_sensitive"] is True


def test_user_raw_materials_extend_commodities() -> None:
    res = build_context(facts(user_provided={"raw_materials": ["Медь"]}))
    assert res.index["commodities"] == ["steel", "nonferrous", "медь"]


def test_missing_and_stub_data_reported_not_invented() -> None:
    res = build_context(
        facts(revenue_rub=None, revenue_stub=True, eco_stage=None, group_revenue_rub=None, group_id=None,
              incoming=DirectionStats(), outgoing=DirectionStats())
    )  # fmt: skip
    assert res.features["revenue_rub"]["value"] is None
    assert "заглушка" in res.features["revenue_rub"]["note"]
    assert res.completeness["stubs"] == ["revenue_rub"]
    assert {"revenue_rub", "eco_stage", "group.id", "counterparties"} <= set(res.completeness["missing"])
    assert res.completeness["score"] < 0.5
    assert res.index["segment"] == "unknown"
    assert res.features["counterparties.unknown_share_in"]["value"] is None


def test_counterparty_features_and_index_tags() -> None:
    res = build_context(facts())
    assert res.features["counterparties.unknown_share_in"]["value"] == 0.4
    assert res.features["counterparties.industry_share_in.construction"]["value"] == 0.28
    assert res.features["counterparties.top_in"]["value"][0]["counterparty_id"] == "U_2"
    # 2% real_estate share is below the index threshold
    assert res.index["counterparty_tags"] == ["construction", "metals"]


def test_segment_uses_group_revenue() -> None:
    assert build_context(facts(group_revenue_rub=Decimal("20000000000"))).index["segment"] == "large"
    assert build_context(facts()).index["segment"] == "mid"


def test_contract_example_paths_are_produced_by_builder() -> None:
    """contracts/examples/tools/get_company_context.response.json must use real feature paths."""
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))["features"]
    produced = set(build_context(facts()).features)
    unknown = {p for p in example if p not in produced}
    assert unknown == set(), f"example uses paths the builder does not produce: {unknown}"
