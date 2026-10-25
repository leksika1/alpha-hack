"""Public API v1 request/response models (Pydantic v2). The full insight body is the contract model."""

from datetime import date as date_type
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.contracts.insight import Role

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- auth


class LoginIn(_Model):
    login: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"login": "demo", "password": "demo12345"}]}
    )


class RefreshIn(_Model):
    refresh_token: str


class TokenOut(_Model):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — token scheme name, not a secret
    expires_in: int


class CompanyBrief(_Model):
    company_id: str
    title: str = Field(
        description="Обезличенное название: вид деятельности и регион (в данных нет наименований)"
    )
    okved_code: str | None
    okved_name: str | None
    region: str | None
    segment: str | None
    role: Role
    is_default: bool


class MeOut(_Model):
    user_id: UUID
    login: str
    display_name: str
    platform_role: Literal["user", "admin"]
    companies: list[CompanyBrief]


class RoleIn(_Model):
    role: Role


class RoleOut(_Model):
    company_id: str
    role: Role
    candidates_pending: int
    regeneration: Literal["queued", "not_needed"]


# ---------------------------------------------------------------- companies


class FeatureOut(_Model):
    path: str
    value: Any
    source: Literal["csv", "derived", "user", "external"]
    note: str | None = None


class ProfileSection(_Model):
    key: str
    title: str
    features: list[FeatureOut]


class ProfileOut(_Model):
    company_id: str
    title: str
    context_version: int
    computed_at: datetime
    sections: list[ProfileSection]
    completeness: dict[str, Any]
    user_provided: dict[str, Any]
    sources_legend: dict[str, str]


Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{3}$")]


class ProfileUpdateIn(_Model):
    """Короткая анкета: только понятные и оправданные сведения (FAQ п.33). null очищает поле."""

    suppliers: Annotated[list[ShortText], Field(max_length=20)] | None = None
    customers: Annotated[list[ShortText], Field(max_length=20)] | None = None
    raw_materials: Annotated[list[ShortText], Field(max_length=20)] | None = None
    currencies: Annotated[list[Currency], Field(max_length=10)] | None = None
    credit_plans: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "raw_materials": ["медь", "сталь"],
                    "currencies": ["CNY"],
                    "credit_plans": "Кредит 300 млн в 2027",
                }
            ]
        },
    )


class ProfileUpdateOut(_Model):
    profile: ProfileOut
    candidates_pending: int
    regeneration: Literal["queued", "not_needed"]


class GraphCounterparty(_Model):
    company_id: str
    known: bool
    okved_name: str | None
    region: str | None
    same_group: bool


class GraphEdge(_Model):
    edge_type: Literal["payment", "shared_person", "hypothesis"]
    label: str
    direction: Literal["in", "out", "undirected"]
    counterparty: GraphCounterparty
    amount_rub: float | None
    share: float | None
    is_hypothesis: bool
    confidence: float
    evidence: dict[str, Any]


class GraphOut(_Model):
    company_id: str
    group: dict[str, Any] | None
    unknown_counterparties: dict[str, float | None]
    edges: list[GraphEdge]


# ---------------------------------------------------------------- feed and insights


class InsightState(_Model):
    seen: bool
    opened: bool
    saved: bool
    hidden: bool
    updated: bool = Field(
        default=False, description="Инсайт перегенерирован после просмотра (новые новости, изменился профиль)"
    )


class InsightCard(_Model):
    insight_id: UUID
    event_id: UUID
    company_id: str
    title: str
    summary: str
    polarity: Literal["risk", "opportunity", "mixed", "neutral"]
    urgent: bool
    impact_level: Literal["low", "medium", "high", "critical"]
    impact_score: int
    relevance_score: float
    role_variant: Role
    role_match: bool = Field(
        description="false — показан вариант для другой роли, пока готовится вариант для текущей"
    )
    event_type: str
    published_at: datetime
    sources_count: int
    bank_view_coverage: Literal["direct", "related", "none"]
    why_for_you: str | None = Field(default=None, description="Первая причина «почему важно вам» из инсайта")
    priority_reasons: list[str] = Field(
        default_factory=list, description="Почему выше в ленте: главные компоненты релевантности, по-русски"
    )
    more_in_topic: int = Field(
        default=0,
        description="Ещё столько карточек на эту макротему (ставка, курс) свёрнуто в виде «Все»; "
        "в фильтрах «Срочные», «Риски», «Возможности», «Отслеживаемые» видны все (ADR-042)",
    )
    state: InsightState


class FeedOut(_Model):
    company_id: str
    role: Role
    items: list[InsightCard]
    next_cursor: str | None


class CompanySummary(_Model):
    company_id: str
    title: str
    unread: int
    urgent: int
    top: InsightCard | None


class FeedSummaryOut(_Model):
    unread: int
    urgent: int
    companies: list[CompanySummary]


class InsightOut(_Model):
    insight_id: UUID
    company_id: str
    created_at: datetime
    role_match: bool
    state: InsightState
    insight: dict[str, Any] = Field(description="Полный инсайт по contracts/insight.schema.json")


class TraceOut(_Model):
    insight_id: UUID
    trace_id: str
    run: dict[str, Any] | None
    steps: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]] = Field(
        description="Вызовы LLM через Gateway (модель, токены, ₽, кэш; превью промпта — после маскирования)"
    )
    company_features: dict[str, Any] = Field(
        description="Значения признаков компании, на которые ссылается инсайт"
    )
    candidate: dict[str, Any] | None
    sources: list[dict[str, Any]]
    statements_by_kind: dict[str, int]
    validation: dict[str, Any]


class StateIn(_Model):
    action: Literal["open", "hide", "unhide", "save", "unsave", "watch"] = Field(
        description="watch — «наблюдать»: сохранить и отметить как выбранный следующий шаг"
    )


class FeedbackIn(_Model):
    useful: bool
    reason: Literal["irrelevant", "wrong", "obvious", "outdated", "other"] | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"useful": False, "reason": "irrelevant", "comment": "Не наш рынок"}]
        },
    )


class FeedbackOut(_Model):
    feedback_id: int
    recorded_at: datetime
    hidden: bool = Field(default=False, description="Инсайт скрыт по отзыву (причина irrelevant)")
    applied: list[str] = Field(
        default_factory=list, description="Что сервис сделал с отзывом — показать пользователю"
    )


class SourceClickIn(_Model):
    source_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]


class SourceClickOut(_Model):
    source_id: str
    type: str
    url: str | None
    title: str
    page: int | None
    note: str | None = None


class ActionIn(_Model):
    type: Literal["contact_manager", "callback", "application"]
    product_id: str | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    preferred_time: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"type": "callback", "product_id": "loan_refinancing", "preferred_time": "завтра 10:00-12:00"}
            ]
        },
    )


class ActionOut(_Model):
    request_id: UUID
    external_ref: str
    status: Literal["new", "in_progress", "done"]
    request_type: str
    product_id: str | None
    message: str
    created_at: datetime


class BankProductOut(_Model):
    product_id: str
    name: str
    description: str
    tags: list[str]
    url: str | None
    source: str


class NewsOut(_Model):
    news_id: UUID
    event_id: UUID | None
    title: str
    summary: str | None
    body: str | None
    url: str
    publisher: str | None
    published_at: datetime
    event_type: str | None
    tags: dict[str, Any]
    flags: list[str]
    is_fixture: bool


class MarketSourceOut(_Model):
    title: str
    url: str | None


class MarketIndicatorOut(_Model):
    indicator: str = Field(description="key_rate, usd_rub, eur_rub, cny_rub")
    title: str
    unit: str
    value: float
    date: date_type = Field(description="Дата значения (курс ЦБ устанавливается на следующий день)")
    previous_value: float | None = Field(description="Предыдущее отличающееся значение")
    previous_date: date_type | None
    change: float | None
    fetched_at: datetime
    source: MarketSourceOut


class MarketOut(_Model):
    items: list[MarketIndicatorOut] = Field(description="Пусто, если данные ЦБ ещё не загружены")
