"""Draft of an insight: what the analyst (LLM or rules) writes before deterministic assembly.

The LLM answers in a compact JSON (prompts/analyst.md) that references evidence IDs (s1, sa1, sc, ...) and
feature paths. `parse_llm_draft` is lenient: unknown enum values are coerced, broken items are dropped — the
assembler and the checks then guarantee the contract. `rule_draft` builds the same structure from templates so
that the service works without LLM (budget exhausted, provider down, allow_llm=false).
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from alfa_ml.contracts import (
    ACTION_TYPES,
    EVENT_TYPES,
    HORIZONS,
    PARTICIPANT_TYPES,
    POLARITIES,
    STATEMENT_KINDS,
)

Kind = Literal["fact", "bank_analytics", "model_inference", "hypothesis"]


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _str_list(value: Any, limit: int = 10) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if isinstance(x, str | int | float) and str(x).strip()][:limit]


class Claim(BaseModel):
    id: str = ""
    text: str
    kind: str = "model_inference"
    confidence: float = 0.6
    sources: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> str:
        return _pick(v, STATEMENT_KINDS, "model_inference")

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.6

    @field_validator("sources", "features", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class Reason(BaseModel):
    text: str
    features: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    factor: str | None = None  # exposure component, set by rules

    @field_validator("features", "claims", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class Consequence(BaseModel):
    text: str
    horizon: str = "months"
    drivers: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)

    @field_validator("horizon", mode="before")
    @classmethod
    def _horizon(cls, v: Any) -> str:
        return _pick(v, HORIZONS, "months")

    @field_validator("drivers", "claims", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class Scenario(BaseModel):
    name: str
    logic: str
    plausibility: str = "medium"
    signals: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)

    @field_validator("plausibility", mode="before")
    @classmethod
    def _pl(cls, v: Any) -> str:
        return _pick(v, ("low", "medium", "high"), "medium")

    @field_validator("signals", "claims", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class BankPosition(BaseModel):
    source: str
    text: str
    quote: str | None = None


class Quant(BaseModel):
    metric: str
    value: float
    unit: str
    basis: str
    period: str | None = None
    features: list[str] = Field(default_factory=list)

    @field_validator("features", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class Action(BaseModel):
    type: str = "analyze"
    text: str
    product_id: str | None = None
    source: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        return _pick(v, ACTION_TYPES, "analyze")


class Fact(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


class Participant(BaseModel):
    name: str
    type: str = "other"

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        return _pick(v, PARTICIPANT_TYPES, "other")


class Draft(BaseModel):
    relevant: bool = True
    skip_reason: str | None = None
    title: str = ""
    summary: str = ""
    polarity: str = "neutral"
    event_summary: str = ""
    event_type: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    key_facts: list[Fact] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    why_it_matters: list[Reason] = Field(default_factory=list)
    risks: list[Consequence] = Field(default_factory=list)
    opportunities: list[Consequence] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    bank_positions: list[BankPosition] = Field(default_factory=list)
    model_disagrees_with_bank: bool = False
    disagreement_note: str | None = None
    impact_level: str | None = None
    impact_rationale: str = ""
    quantitative: list[Quant] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    author: str = "rules@1"  # rules@1 | <model display name>

    @field_validator("polarity", mode="before")
    @classmethod
    def _pol(cls, v: Any) -> str:
        return _pick(v, POLARITIES, "neutral")

    @field_validator("event_type", mode="before")
    @classmethod
    def _et(cls, v: Any) -> str | None:
        return _pick(v, EVENT_TYPES, "other") if v else None

    @field_validator("impact_level", mode="before")
    @classmethod
    def _lvl(cls, v: Any) -> str | None:
        return _pick(v, ("low", "medium", "high", "critical"), "") or None if v else None

    def claim_ids(self) -> set[str]:
        return {c.id for c in self.claims}


def _items(raw: Any, model: type[BaseModel], limit: int) -> list[Any]:
    out: list[Any] = []
    if not isinstance(raw, list):
        return out
    for item in raw[: limit * 2]:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model.model_validate(item))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


# the analyst sometimes quotes feature paths in the prose: "(sensitivities.consumer_demand_dependent)"
_PATH_IN_TEXT = re.compile(
    r"\s*\((?:[a-z_]+\.)+[a-z0-9_]+(?:,\s*(?:[a-z_]+\.)+[a-z0-9_]+)*\)|`(?:[a-z_]+\.)+[a-z0-9_]+`"
)
_PROSE_KEYS = {
    "title",
    "summary",
    "text",
    "logic",
    "impact_rationale",
    "event_summary",
    "disagreement_note",
    "name",
}


def _clean_prose(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _clean_prose(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_prose(v, key) for v in value]
    if isinstance(value, str) and key in _PROSE_KEYS:
        return re.sub(r"\s{2,}", " ", _PATH_IN_TEXT.sub("", value)).strip()
    return value


def parse_llm_draft(raw: dict[str, Any], author: str) -> Draft:
    """Lenient parse of the analyst JSON; never raises on item-level problems."""
    raw = _clean_prose(raw)
    claims = _items(raw.get("claims"), Claim, 16)
    for i, c in enumerate(claims, start=1):
        if not c.id or not c.id[0].isalpha():
            c.id = f"c{i}"
    base = {
        k: raw.get(k)
        for k in (
            "relevant",
            "skip_reason",
            "title",
            "summary",
            "polarity",
            "event_summary",
            "event_type",
            "model_disagrees_with_bank",
            "disagreement_note",
            "impact_level",
            "impact_rationale",
        )
        if raw.get(k) is not None
    }
    for key in ("title", "summary", "event_summary", "impact_rationale", "skip_reason", "disagreement_note"):
        if key in base:
            base[key] = str(base[key]).strip()
    if "relevant" in base:
        base["relevant"] = base["relevant"] not in (False, "false", "False", 0, "0", "no")
    if "model_disagrees_with_bank" in base:
        base["model_disagrees_with_bank"] = base["model_disagrees_with_bank"] in (True, "true", "True", 1)
    draft = Draft.model_validate(base)
    draft.author = author
    draft.claims = claims
    draft.participants = _items(raw.get("participants"), Participant, 6)
    draft.key_facts = _items(raw.get("key_facts"), Fact, 5)
    draft.why_it_matters = _items(raw.get("why_it_matters"), Reason, 6)
    draft.risks = _items(raw.get("risks"), Consequence, 5)
    draft.opportunities = _items(raw.get("opportunities"), Consequence, 5)
    draft.scenarios = _items(raw.get("scenarios"), Scenario, 3)
    draft.bank_positions = _items(raw.get("bank_positions"), BankPosition, 3)
    draft.quantitative = _items(raw.get("quantitative"), Quant, 4)
    draft.actions = _items(raw.get("actions"), Action, 6)
    return draft
