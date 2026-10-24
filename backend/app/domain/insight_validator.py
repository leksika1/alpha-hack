"""Insight validator: JSON Schema (contracts/insight.schema.json) + business rules (contracts/README.md).

An invalid insight never reaches the feed; it is stored in insights_rejected with the reasons.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.domain.compliance import check_texts, strings_of
from app.domain.contracts import CONTRACTS_DIR
from app.domain.contracts.insight import NOT_INVESTMENT_ADVICE, Insight, StatementKind
from app.domain.impact import level_for_score

SOURCE_REQUIRED_KINDS = {StatementKind.fact, StatementKind.bank_analytics}


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    detail: str


@dataclass(slots=True)
class ValidationResult:
    insight: Insight | None
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.insight is not None and not self.problems

    def reasons(self) -> list[dict[str, str]]:
        return [{"code": p.code, "detail": p.detail} for p in self.problems]


@dataclass(frozen=True, slots=True)
class ValidationContext:
    company_id: str
    event_id: str
    feature_paths: frozenset[str]
    known_products: frozenset[str] = frozenset()
    chunk_exists: Callable[[str], bool] = lambda _chunk_id: False


@lru_cache
def _schema_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS_DIR / "insight.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _feature_known(ref: str, paths: frozenset[str]) -> bool:
    return ref in paths or any(p.startswith(ref + ".") for p in paths)


def _refs(items: Iterable[Any], attr: str) -> list[str]:
    out: list[str] = []
    for item in items:
        out.extend(getattr(item, attr))
    return out


def validate_insight(payload: dict[str, Any], ctx: ValidationContext) -> ValidationResult:
    problems: list[Problem] = []
    schema_errors = sorted(_schema_validator().iter_errors(payload), key=lambda e: list(e.path))
    for err in schema_errors[:20]:
        path = "/".join(str(p) for p in err.path) or "<root>"
        problems.append(Problem("schema", f"{path}: {err.message[:300]}"))
    if problems:
        return ValidationResult(None, problems)
    try:
        insight = Insight.model_validate(payload)
    except ValidationError as exc:  # schema and model are kept in sync, but be defensive
        return ValidationResult(None, [Problem("schema", str(exc)[:500])])

    if insight.company_id != ctx.company_id:
        problems.append(Problem("company_mismatch", f"{insight.company_id} != {ctx.company_id}"))
    if str(insight.event_id) != ctx.event_id:
        problems.append(Problem("event_mismatch", f"{insight.event_id} != {ctx.event_id}"))
    if not insight.has_mandatory_disclaimer():
        problems.append(Problem("missing_disclaimer", NOT_INVESTMENT_ADVICE))

    source_ids = {s.id for s in insight.sources}
    statement_ids = {s.id for s in insight.statements}
    if len(statement_ids) != len(insight.statements):
        problems.append(Problem("duplicate_statement_id", "statements[].id must be unique"))
    if len(source_ids) != len(insight.sources):
        problems.append(Problem("duplicate_source_id", "sources[].id must be unique"))

    for st in insight.statements:
        if st.kind in SOURCE_REQUIRED_KINDS and not st.source_refs:
            problems.append(Problem("fact_without_source", f"{st.id} ({st.kind.value})"))
    all_source_refs = (
        _refs(insight.statements, "source_refs")
        + _refs(insight.event.key_facts, "source_refs")
        + [p.source_ref for p in insight.bank_view.positions]
        + [a.source_ref for a in insight.actions if a.source_ref]
    )
    for ref in sorted(set(all_source_refs) - source_ids):
        problems.append(Problem("unknown_source_ref", ref))

    statement_refs = (
        _refs(insight.why_it_matters, "statement_refs")
        + _refs(insight.consequences.risks, "statement_refs")
        + _refs(insight.consequences.opportunities, "statement_refs")
        + _refs(insight.consequences.scenarios, "statement_refs")
    )
    for ref in sorted(set(statement_refs) - statement_ids):
        problems.append(Problem("unknown_statement_ref", ref))

    feature_refs = (
        _refs(insight.why_it_matters, "feature_refs")
        + _refs(insight.statements, "feature_refs")
        + _refs(insight.impact.quantitative, "feature_refs")
        + list(insight.relevance.matched_features)
    )
    for ref in sorted({r for r in feature_refs if not _feature_known(r, ctx.feature_paths)}):
        problems.append(Problem("unknown_feature_ref", ref))

    for pos in insight.bank_view.positions:
        if not ctx.chunk_exists(str(pos.analytics_chunk_id)):
            problems.append(Problem("unknown_analytics_chunk", str(pos.analytics_chunk_id)))
    bv = insight.bank_view
    if (bv.coverage == "none") != (not bv.positions):
        problems.append(
            Problem("bank_view_inconsistent", f"coverage={bv.coverage}, positions={len(bv.positions)}")
        )
    if bv.model_disagrees_with_bank and not (bv.disagreement_note or "").strip():
        problems.append(Problem("disagreement_without_note", "model_disagrees_with_bank=true"))

    expected = level_for_score(insight.impact.score)
    if insight.impact.level != expected:
        problems.append(
            Problem(
                "impact_level_mismatch",
                f"score {insight.impact.score} -> {expected.value}, got {insight.impact.level.value}",
            )
        )
    for finding in check_texts(strings_of(payload)):  # Russian law (ADR-037)
        problems.append(Problem(f"compliance_{finding.code}", f"{finding.law}: {finding.fragment}"))
    for action in insight.actions:
        if action.product_id and action.product_id not in ctx.known_products:
            problems.append(Problem("unknown_product", action.product_id))
    return ValidationResult(insight, problems)
