"""Contracts are the hand-off to the ML and frontend teams: they must be valid, self-consistent,
and mirrored exactly by the Pydantic models used by the backend."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from openapi_spec_validator import validate as validate_openapi
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app.domain.contracts import CONTRACTS_DIR
from app.domain.contracts import insight as m

SCHEMA_PATH = CONTRACTS_DIR / "insight.schema.json"
EXAMPLES = CONTRACTS_DIR / "examples"
OPENAPI_FILES = ["ml-service.openapi.yaml", "tools-api.openapi.yaml", "llm-gateway.openapi.yaml"]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return _load_json(SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.fixture
def valid_insight() -> dict[str, Any]:
    return _load_json(EXAMPLES / "insight.valid.json")


def test_schema_is_valid_draft_2020_12(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)


def test_valid_example_passes_schema_and_pydantic(
    validator: Draft202012Validator, valid_insight: dict[str, Any]
) -> None:
    validator.validate(valid_insight)
    parsed = m.Insight.model_validate(valid_insight)
    assert parsed.has_mandatory_disclaimer()
    assert parsed.schema_version.startswith("1.")


def _drop_mandatory_disclaimer(d: dict[str, Any]) -> None:
    d["disclaimers"] = [x for x in d["disclaimers"] if x["code"] != "not_investment_advice"]


def _bad_kind(d: dict[str, Any]) -> None:
    d["statements"][0]["kind"] = "opinion"


def _score_out_of_range(d: dict[str, Any]) -> None:
    d["impact"]["score"] = 150


def _reason_without_features(d: dict[str, Any]) -> None:
    d["why_it_matters"][0]["feature_refs"] = []


def _extra_field(d: dict[str, Any]) -> None:
    d["unexpected"] = True


def _missing_bank_view(d: dict[str, Any]) -> None:
    del d["bank_view"]


def _bad_company_id(d: dict[str, Any]) -> None:
    d["company_id"] = "428697"


def _bad_role(d: dict[str, Any]) -> None:
    d["role_variant"] = "cto"


def _empty_sources(d: dict[str, Any]) -> None:
    d["sources"] = []


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_mandatory_disclaimer,
        _bad_kind,
        _score_out_of_range,
        _reason_without_features,
        _extra_field,
        _missing_bank_view,
        _bad_company_id,
        _bad_role,
        _empty_sources,
    ],
)
def test_invalid_insights_rejected_by_both(
    mutate: Any, validator: Draft202012Validator, valid_insight: dict[str, Any]
) -> None:
    doc = copy.deepcopy(valid_insight)
    mutate(doc)
    with pytest.raises(ValidationError):
        validator.validate(doc)
    with pytest.raises((PydanticValidationError, ValueError)):
        insight = m.Insight.model_validate(doc)
        if not insight.has_mandatory_disclaimer():
            raise ValueError("mandatory disclaimer missing")


# $defs name -> mirroring Pydantic model
DEF_TO_MODEL: dict[str, type[BaseModel]] = {
    "event": m.EventInfo,
    "reason": m.Reason,
    "consequence": m.Consequence,
    "scenario": m.Scenario,
    "consequences": m.Consequences,
    "quantitative": m.Quantitative,
    "impact": m.Impact,
    "action": m.Action,
    "bank_position": m.BankPosition,
    "bank_view": m.BankView,
    "statement": m.Statement,
    "relevance": m.Relevance,
    "source": m.Source,
    "disclaimer": m.Disclaimer,
    "model_used": m.ModelUsed,
}


def _assert_mirror(schema_obj: dict[str, Any], model: type[BaseModel], where: str) -> None:
    schema_props = set(schema_obj["properties"])
    schema_required = set(schema_obj.get("required", []))
    model_fields = set(model.model_fields)
    model_required = {name for name, f in model.model_fields.items() if f.is_required()}
    assert schema_props == model_fields, f"{where}: properties differ {schema_props ^ model_fields}"
    assert schema_required == model_required, f"{where}: required differ {schema_required ^ model_required}"


def test_pydantic_mirrors_schema_top_level(schema: dict[str, Any]) -> None:
    _assert_mirror(schema, m.Insight, "Insight")


@pytest.mark.parametrize("def_name", sorted(DEF_TO_MODEL))
def test_pydantic_mirrors_schema_defs(schema: dict[str, Any], def_name: str) -> None:
    _assert_mirror(schema["$defs"][def_name], DEF_TO_MODEL[def_name], def_name)


def test_participant_mirror(schema: dict[str, Any]) -> None:
    _assert_mirror(
        schema["$defs"]["event"]["properties"]["participants"]["items"], m.Participant, "participant"
    )


def test_enums_match_schema(schema: dict[str, Any]) -> None:
    defs = schema["$defs"]
    assert set(defs["role"]["enum"]) == {r.value for r in m.Role}
    assert set(defs["statement"]["properties"]["kind"]["enum"]) == {k.value for k in m.StatementKind}
    assert set(defs["impact"]["properties"]["level"]["enum"]) == {k.value for k in m.ImpactLevel}
    assert set(defs["action"]["properties"]["type"]["enum"]) == {k.value for k in m.ActionType}
    assert set(defs["source"]["properties"]["type"]["enum"]) == {k.value for k in m.SourceType}
    assert set(defs["event"]["properties"]["event_type"]["enum"]) == {k.value for k in m.EventType}


@pytest.mark.parametrize("name", OPENAPI_FILES)
def test_openapi_contracts_are_valid(name: str) -> None:
    path = CONTRACTS_DIR / name
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    validate_openapi(spec, base_uri=path.as_uri())


def _registry() -> Registry:
    resources = [(SCHEMA_PATH.as_uri(), DRAFT202012.create_resource(_load_json(SCHEMA_PATH)))]
    for name in OPENAPI_FILES:
        path = CONTRACTS_DIR / name
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        resources.append((path.as_uri(), Resource.from_contents(doc, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


def _validate_component(example: Any, openapi_file: str, component: str) -> None:
    ref = f"{(CONTRACTS_DIR / openapi_file).as_uri()}#/components/schemas/{component}"
    Draft202012Validator({"$ref": ref}, registry=_registry(), format_checker=FormatChecker()).validate(
        example
    )


@pytest.mark.parametrize(
    ("example", "openapi_file", "component"),
    [
        ("generate.request.json", "ml-service.openapi.yaml", "GenerateRequest"),
        ("generate.response.skipped.json", "ml-service.openapi.yaml", "GenerateResult"),
        ("chat.request.json", "ml-service.openapi.yaml", "ChatRequest"),
        ("score.request.json", "ml-service.openapi.yaml", "ScoreRequest"),
        ("tools/get_company_context.response.json", "tools-api.openapi.yaml", "CompanyContext"),
        ("tools/get_event.response.json", "tools-api.openapi.yaml", "Event"),
        ("tools/log_step.request.json", "tools-api.openapi.yaml", "AgentStep"),
    ],
)
def test_examples_match_components(example: str, openapi_file: str, component: str) -> None:
    _validate_component(_load_json(EXAMPLES / example), openapi_file, component)


def test_completed_generate_result_embeds_valid_insight(valid_insight: dict[str, Any]) -> None:
    result = {"request_id": "r1", "status": "completed", "insight": valid_insight}
    _validate_component(result, "ml-service.openapi.yaml", "GenerateResult")
    broken = copy.deepcopy(result)
    _drop_mandatory_disclaimer(broken["insight"])
    with pytest.raises(ValidationError):
        _validate_component(broken, "ml-service.openapi.yaml", "GenerateResult")


def test_rag_example_hits_match_component() -> None:
    for hit in _load_json(EXAMPLES / "tools" / "search_bank_analytics.response.json")["hits"]:
        _validate_component(hit, "tools-api.openapi.yaml", "AnalyticsHit")


def test_chat_sse_example_final_event_matches_component() -> None:
    lines = (EXAMPLES / "chat.sse.txt").read_text(encoding="utf-8").splitlines()
    events = [
        (lines[i].removeprefix("event: "), json.loads(lines[i + 1].removeprefix("data: ")))
        for i in range(len(lines) - 1)
        if lines[i].startswith("event: ")
    ]
    assert [e for e, _ in events][-1] == "done"
    _validate_component(events[-1][1], "ml-service.openapi.yaml", "ChatFinal")
    for name, payload in events:
        if name == "source":
            Draft202012Validator(
                {"$ref": f"{SCHEMA_PATH.as_uri()}#/$defs/source"}, registry=_registry()
            ).validate(payload)


def test_example_feature_refs_exist_in_context_example(valid_insight: dict[str, Any]) -> None:
    """Every feature_ref in the insight example must be resolvable in the get_company_context example."""
    ctx = _load_json(EXAMPLES / "tools" / "get_company_context.response.json")["features"]
    refs: set[str] = set()
    for reason in valid_insight["why_it_matters"]:
        refs.update(reason["feature_refs"])
    for st in valid_insight["statements"]:
        refs.update(st.get("feature_refs", []))
    missing = {r for r in refs if r not in ctx and not any(k.startswith(r + ".") for k in ctx)}
    assert missing == set(), f"feature_refs not present in context example: {missing}"
