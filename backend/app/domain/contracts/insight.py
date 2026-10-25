"""Pydantic mirror of contracts/insight.schema.json (schema_version 1.x).

Keep in sync with the JSON Schema: tests/unit/test_contracts.py compares required/optional
fields of every model with the corresponding schema definition.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "1.0.0"
IMPACT_SCALE_VERSION = 1
NOT_INVESTMENT_ADVICE = "not_investment_advice"

IdRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
FeatureRef = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*$")]
CompanyId = Annotated[str, StringConstraints(pattern=r"^U_[0-9]+$")]
Text3 = Annotated[str, StringConstraints(min_length=3)]
Text10 = Annotated[str, StringConstraints(min_length=10)]


class Role(StrEnum):
    owner = "owner"
    ceo = "ceo"
    cfo = "cfo"
    chief_accountant = "chief_accountant"
    treasurer = "treasurer"
    other = "other"


class EventType(StrEnum):
    monetary_policy = "monetary_policy"
    fx = "fx"
    commodities = "commodities"
    sanctions = "sanctions"
    regulation_tax = "regulation_tax"
    trade_logistics = "trade_logistics"
    industry = "industry"
    company = "company"
    macro_stat = "macro_stat"
    labor = "labor"
    other = "other"


class StatementKind(StrEnum):
    fact = "fact"
    bank_analytics = "bank_analytics"
    model_inference = "model_inference"
    hypothesis = "hypothesis"


class ImpactLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ActionType(StrEnum):
    contact_manager = "contact_manager"
    callback = "callback"
    application = "application"
    analyze = "analyze"
    monitor = "monitor"
    read_source = "read_source"


class SourceType(StrEnum):
    news = "news"
    bank_analytics = "bank_analytics"
    company_data = "company_data"
    user_input = "user_input"
    external_stat = "external_stat"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Participant(_Strict):
    name: Annotated[str, StringConstraints(min_length=1)]
    type: Literal["company", "government", "regulator", "country", "public_person", "industry", "other"]
    company_id: CompanyId | None = None


class KeyFact(_Strict):
    text: Text3
    source_refs: list[IdRef] = Field(min_length=1)


class EventInfo(_Strict):
    title: Annotated[str, StringConstraints(min_length=3, max_length=300)]
    summary: Annotated[str, StringConstraints(min_length=10, max_length=1500)]
    event_type: EventType
    occurred_at: datetime | None = None
    participants: list[Participant] = Field(default_factory=list)
    key_facts: list[KeyFact] = Field(min_length=1)


class Reason(_Strict):
    text: Text10
    feature_refs: list[FeatureRef] = Field(min_length=1)
    statement_refs: list[IdRef] = Field(default_factory=list)


class Consequence(_Strict):
    text: Text10
    horizon: Literal["days", "weeks", "months", "quarters", "year_plus"]
    drivers: list[Text3] = Field(default_factory=list)
    statement_refs: list[IdRef] = Field(default_factory=list)


class Scenario(_Strict):
    name: Text3
    logic: Text10
    plausibility: Literal["low", "medium", "high"]
    signals: list[str] = Field(default_factory=list)
    statement_refs: list[IdRef] = Field(default_factory=list)


class Consequences(_Strict):
    risks: list[Consequence]
    opportunities: list[Consequence]
    scenarios: list[Scenario] = Field(default_factory=list)


class Quantitative(_Strict):
    metric: Annotated[str, StringConstraints(min_length=2)]
    value: float
    unit: Annotated[str, StringConstraints(min_length=1)]
    basis: Text10
    period: str | None = None
    feature_refs: list[FeatureRef] = Field(default_factory=list)


class Impact(_Strict):
    level: ImpactLevel
    score: int = Field(ge=0, le=100)
    scale_version: Literal[1]
    rationale: Text10
    quantitative: list[Quantitative] = Field(default_factory=list)


class Action(_Strict):
    type: ActionType
    text: Annotated[str, StringConstraints(min_length=5)]
    product_id: str | None = None
    source_ref: IdRef | None = None


class BankPosition(_Strict):
    text: Text10
    quote: Annotated[str, StringConstraints(max_length=600)] | None = None
    analytics_chunk_id: UUID
    document_id: UUID
    page: int = Field(ge=1)
    source_ref: IdRef


class BankView(_Strict):
    coverage: Literal["direct", "related", "none"]
    positions: list[BankPosition]
    model_disagrees_with_bank: bool
    disagreement_note: str | None = None


class Statement(_Strict):
    id: IdRef
    text: Text3
    kind: StatementKind
    confidence: float = Field(ge=0, le=1)
    source_refs: list[IdRef]
    feature_refs: list[FeatureRef] = Field(default_factory=list)


class Relevance(_Strict):
    score: float = Field(ge=0, le=1)
    breakdown: dict[str, float]
    matched_features: list[FeatureRef] = Field(default_factory=list)
    scorer: str | None = None


class Source(_Strict):
    id: IdRef
    type: SourceType
    title: Annotated[str, StringConstraints(min_length=1)]
    ref_id: str | None = None
    url: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    page: int | None = Field(default=None, ge=1)


class Disclaimer(_Strict):
    code: Literal[
        "not_investment_advice",
        "model_generated",
        "model_disagrees_with_bank",
        "incomplete_data",
        "hypothesis_present",
    ]
    text: Annotated[str, StringConstraints(min_length=5)]


class ModelUsed(_Strict):
    name: str
    purpose: Literal[
        "generation", "planning", "self_check", "embedding", "rerank", "scoring", "ocr", "template"
    ]


class Insight(_Strict):
    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.[0-9]+\.[0-9]+$")]
    insight_id: UUID | None = None
    company_id: CompanyId
    event_id: UUID
    role_variant: Role
    trace_id: Annotated[str, StringConstraints(min_length=1)]
    language: Literal["ru"] = "ru"
    title: Annotated[str, StringConstraints(min_length=5, max_length=160)]
    summary: Annotated[str, StringConstraints(min_length=20, max_length=600)]
    polarity: Literal["risk", "opportunity", "mixed", "neutral"]
    urgent: bool
    event: EventInfo
    why_it_matters: list[Reason] = Field(min_length=1, max_length=6)
    consequences: Consequences
    impact: Impact
    actions: list[Action] = Field(max_length=6)
    bank_view: BankView
    statements: list[Statement] = Field(min_length=1)
    relevance: Relevance
    sources: list[Source] = Field(min_length=1)
    disclaimers: list[Disclaimer] = Field(min_length=1)
    models_used: list[ModelUsed]
    generated_at: datetime
    expires_at: datetime | None = None

    def has_mandatory_disclaimer(self) -> bool:
        return any(d.code == NOT_INVESTMENT_ADVICE for d in self.disclaimers)
