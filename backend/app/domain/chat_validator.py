"""Check of the agent's final answer to a question (contracts/ml-service.openapi.yaml#ChatFinal).

The answer text is the ML agent's; the backend only checks the contract and the traceability rules (as for
insights): statements of kind fact / bank_analytics must cite sources of the answer. The mandatory
"not investment advice" disclaimer is appended by the backend when the agent omitted it.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app.domain.contracts import CONTRACTS_DIR

SOURCE_REQUIRED_KINDS = {"fact", "bank_analytics"}
NOT_INVESTMENT_ADVICE = {
    "code": "not_investment_advice",
    "text": "Материал носит информационный характер и не является инвестиционной рекомендацией.",
}


@dataclass(slots=True)
class ChatCheck:
    final: dict[str, Any]
    problems: list[dict[str, str]] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@lru_cache
def _validator() -> Draft202012Validator:
    import json

    insight_path = CONTRACTS_DIR / "insight.schema.json"
    ml_path = CONTRACTS_DIR / "ml-service.openapi.yaml"
    registry = Registry().with_resources(
        [
            (insight_path.as_uri(), DRAFT202012.create_resource(json.loads(insight_path.read_text("utf-8")))),
            (
                ml_path.as_uri(),
                Resource.from_contents(
                    yaml.safe_load(ml_path.read_text("utf-8")), default_specification=DRAFT202012
                ),
            ),
        ]
    )
    ref = f"{ml_path.as_uri()}#/components/schemas/ChatFinal"
    return Draft202012Validator({"$ref": ref}, registry=registry, format_checker=FormatChecker())


def check_chat_final(final: dict[str, Any]) -> ChatCheck:
    result = ChatCheck(final=dict(final))
    for err in _validator().iter_errors(final):
        path = "/".join(str(p) for p in err.absolute_path) or "(root)"
        result.problems.append({"code": "schema", "detail": f"{path}: {err.message}"[:300]})
    if result.problems:
        return result
    source_ids = {s["id"] for s in final.get("sources", [])}
    for st in final.get("statements", []):
        refs = set(st.get("source_refs") or [])
        if st.get("kind") in SOURCE_REQUIRED_KINDS and (not refs or not refs <= source_ids):
            result.problems.append(
                {
                    "code": "unsupported-statement",
                    "detail": f"{st.get('id')}: {st.get('kind')} without valid sources",
                }
            )
    disclaimers = list(final.get("disclaimers") or [])
    if not any(d.get("code") == NOT_INVESTMENT_ADVICE["code"] for d in disclaimers):
        result.final["disclaimers"] = [*disclaimers, NOT_INVESTMENT_ADVICE]
        result.fixes.append("added not_investment_advice disclaimer")
    return result
