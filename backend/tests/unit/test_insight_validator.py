import copy
import json
from typing import Any

import pytest

from app.domain.contracts import CONTRACTS_DIR
from app.domain.insight_validator import ValidationContext, validate_insight

EXAMPLE = json.loads((CONTRACTS_DIR / "examples" / "insight.valid.json").read_text(encoding="utf-8"))
CTX_FEATURES = json.loads(
    (CONTRACTS_DIR / "examples" / "tools" / "get_company_context.response.json").read_text(encoding="utf-8")
)["features"]
CHUNK = EXAMPLE["bank_view"]["positions"][0]["analytics_chunk_id"]


def ctx(**kw: Any) -> ValidationContext:
    base: dict[str, Any] = {
        "company_id": EXAMPLE["company_id"],
        "event_id": EXAMPLE["event_id"],
        "feature_paths": frozenset(CTX_FEATURES),
        "known_products": frozenset({"loan_working_capital"}),
        "chunk_exists": lambda c: c == CHUNK,
    }
    base.update(kw)
    return ValidationContext(**base)


def codes(doc: dict[str, Any], **kw: Any) -> set[str]:
    return {p.code for p in validate_insight(doc, ctx(**kw)).problems}


def test_contract_example_is_valid() -> None:
    res = validate_insight(copy.deepcopy(EXAMPLE), ctx())
    assert res.ok, res.reasons()


def mutated(fn: Any) -> dict[str, Any]:
    doc = copy.deepcopy(EXAMPLE)
    fn(doc)
    return doc


def test_schema_errors_are_reported() -> None:
    doc = mutated(lambda d: d["statements"][0].update(kind="opinion"))
    assert codes(doc) == {"schema"}


def test_fact_without_source() -> None:
    doc = mutated(lambda d: d["statements"][0].update(source_refs=[]))
    assert "fact_without_source" in codes(doc)


def test_unknown_source_and_statement_refs() -> None:
    doc = mutated(
        lambda d: (
            d["statements"][1].update(source_refs=["s9"]),
            d["why_it_matters"][0].update(statement_refs=["st99"]),
        )
    )
    assert {"unknown_source_ref", "unknown_statement_ref"} <= codes(doc)


def test_unknown_feature_ref() -> None:
    doc = mutated(lambda d: d["why_it_matters"][0].update(feature_refs=["products.crypto"]))
    assert "unknown_feature_ref" in codes(doc)


def test_unknown_analytics_chunk() -> None:
    assert "unknown_analytics_chunk" in codes(copy.deepcopy(EXAMPLE), chunk_exists=lambda c: False)


def test_impact_level_must_match_score() -> None:
    doc = mutated(lambda d: d["impact"].update(score=10))
    assert "impact_level_mismatch" in codes(doc)


def test_disagreement_requires_note_and_bank_view_consistency() -> None:
    doc = mutated(lambda d: d["bank_view"].update(model_disagrees_with_bank=True, disagreement_note=None))
    assert "disagreement_without_note" in codes(doc)
    doc2 = mutated(lambda d: d["bank_view"].update(coverage="none"))
    assert "bank_view_inconsistent" in codes(doc2)


@pytest.mark.parametrize(
    ("field", "code"), [("company_id", "company_mismatch"), ("event_id", "event_mismatch")]
)
def test_ids_must_match_the_run(field: str, code: str) -> None:
    other = {"company_id": "U_1", "event_id": "00000000-0000-4000-8000-000000000000"}[field]
    assert code in codes(copy.deepcopy(EXAMPLE), **{field: other})


def test_unknown_product() -> None:
    assert "unknown_product" in codes(copy.deepcopy(EXAMPLE), known_products=frozenset())


def test_missing_disclaimer_is_a_schema_error() -> None:
    doc = mutated(
        lambda d: d.update(disclaimers=[x for x in d["disclaimers"] if x["code"] != "not_investment_advice"])
    )
    assert codes(doc) == {"schema"}
