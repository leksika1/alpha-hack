"""English news get the same tags as Russian ones (ADR-039), so foreign sources match companies."""

from app.domain.tagging import detect_language, tag_news


def test_central_bank_rate_decision() -> None:
    tags = tag_news(
        "Federal Reserve cuts interest rate to 4.25%", "The U.S. central bank lowered borrowing costs."
    )
    assert tags.event_type == "monetary_policy" and tags.direction == "down"
    assert tags.numbers[0] == {"kind": "key_rate", "value": 4.25, "unit": "%"}
    assert "США" in tags.countries and tags.language == "en"


def test_bank_of_russia_decision_is_urgent() -> None:
    tags = tag_news("Bank of Russia keeps key rate unchanged at 16%", "The regulator held rates.")
    assert tags.event_type == "monetary_policy" and tags.direction == "hold"
    assert "cbr_key_rate_decision" in tags.urgency_reasons


def test_steel_news_from_china() -> None:
    tags = tag_news(
        "Steel rebar prices climb as Chinese mills curb output", "Steelmakers in China raised quotes."
    )
    assert {"steel", "metals"} <= set(tags.industry_tags) and "steel" in tags.commodities
    assert tags.countries == ["КИТАЙ"] and tags.direction == "up"


def test_sanctions_on_lng() -> None:
    tags = tag_news("EU agrees new sanctions on LNG imports", "The embargo targets natural gas shipments.")
    assert tags.event_type == "sanctions" and "sanctions" in tags.urgency_reasons
    assert "gas" in tags.commodities


def test_grain_and_logistics() -> None:
    wheat = tag_news("Wheat export prices fell in India", "Grain traders expect lower demand.")
    assert (
        "grain" in wheat.commodities and "agriculture" in wheat.industry_tags and "ИНДИЯ" in wheat.countries
    )
    freight = tag_news(
        "Container freight rates from China surge", "Shipping lines added surcharges at the port of Shanghai."
    )
    assert {"logistics", "shipping"} <= set(freight.industry_tags)


def test_short_roots_do_not_overmatch() -> None:
    tags = tag_news("Portfolio managers at FedEx review taxes", "Investors rebalanced portfolios.")
    assert "shipping" not in tags.industry_tags and tags.event_type != "monetary_policy"


def test_language_detection() -> None:
    assert detect_language("цены на сталь выросли") == "ru"
    assert detect_language("steel prices rose") == "en"
    assert detect_language("цены на lng выросли на 5%") == "ru"
    assert tag_news("Цены на сталь выросли", "Металлурги подняли цены.").language == "ru"
