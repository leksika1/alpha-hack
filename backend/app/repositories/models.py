"""SQLAlchemy ORM models. Migrations are generated from this metadata (alembic --autogenerate) and
reviewed by hand; tests/integration/test_schema.py checks there is no drift.

Schemas: ``staging`` — raw CSV rows as text (COPY target); ``core`` — cleaned, typed data.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(20, 2)
# Dimension of stored embeddings; changing it = migration + re-indexing (ADR-006).
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: ARRAY(Text)}


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------- staging (raw text, COPY target)


class StagingCompanyRaw(Base):
    __tablename__ = "companies_raw"
    __table_args__ = {"schema": "staging"}

    row_num: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    synthetic_u: Mapped[str | None] = mapped_column(Text)
    org_type: Mapped[str | None] = mapped_column(Text)
    okfs_name: Mapped[str | None] = mapped_column(Text)
    company_age_years_gradation: Mapped[str | None] = mapped_column(Text)
    eco_mark_label: Mapped[str | None] = mapped_column(Text)
    mainokved_code: Mapped[str | None] = mapped_column(Text)
    mainokved_name: Mapped[str | None] = mapped_column(Text)
    negativemark: Mapped[str | None] = mapped_column(Text)
    amount_workers_fns_gradation: Mapped[str | None] = mapped_column(Text)
    amount_workers_spark_gradation: Mapped[str | None] = mapped_column(Text)
    cl_type: Mapped[str | None] = mapped_column(Text)
    cl_product_rko_mark: Mapped[str | None] = mapped_column(Text)
    cl_product_gar_mark: Mapped[str | None] = mapped_column(Text)
    cl_product_loa_mark: Mapped[str | None] = mapped_column(Text)
    cl_product_lea_mark: Mapped[str | None] = mapped_column(Text)
    cl_product_dep_mark: Mapped[str | None] = mapped_column(Text)
    cl_product_zpp_mark: Mapped[str | None] = mapped_column(Text)
    fts_flag: Mapped[str | None] = mapped_column(Text)
    fts_country_name: Mapped[str | None] = mapped_column(Text)
    factoring_mark: Mapped[str | None] = mapped_column(Text)
    revenue_company: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)


class StagingPaymentRaw(Base):
    __tablename__ = "payments_raw"
    __table_args__ = {"schema": "staging"}

    row_num: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    beneficiary: Mapped[str | None] = mapped_column(Text)
    payer: Mapped[str | None] = mapped_column(Text)
    pay_amt: Mapped[str | None] = mapped_column(Text)
    pay_cnt: Mapped[str | None] = mapped_column(Text)


class StagingPersonLinkRaw(Base):
    __tablename__ = "person_links_raw"
    __table_args__ = {"schema": "staging"}

    row_num: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    synthetic_f: Mapped[str | None] = mapped_column(Text)
    city_name: Mapped[str | None] = mapped_column(Text)
    rc_name: Mapped[str | None] = mapped_column(Text)
    synthetic_u: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- core: runs and data quality


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQualityCheck(Base):
    __tablename__ = "data_quality_report"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.ingest_runs.id", ondelete="CASCADE"), index=True
    )
    dataset: Mapped[str] = mapped_column(String(32))
    check_name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))  # info | warn | error
    value: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core: reference


class OkvedDict(Base):
    __tablename__ = "okved_dict"
    __table_args__ = {"schema": "core"}

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    class_code: Mapped[str | None] = mapped_column(String(2))
    group_code: Mapped[str | None] = mapped_column(String(8))
    section: Mapped[str | None] = mapped_column(String(1), index=True)
    section_name: Mapped[str | None] = mapped_column(Text)
    industry_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    commodities: Mapped[list[str]] = mapped_column(server_default="{}")
    consumer_demand: Mapped[bool] = mapped_column(Boolean, server_default="false")
    import_dependent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    export_oriented: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rate_sensitive_industry: Mapped[bool] = mapped_column(Boolean, server_default="false")
    known_class: Mapped[bool] = mapped_column(Boolean, server_default="true")


# ---------------------------------------------------------------- core: companies


class CompanyGroup(Base):
    __tablename__ = "company_groups"
    __table_args__ = {"schema": "core"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    valid_size: Mapped[int] = mapped_column(Integer)
    client_count: Mapped[int] = mapped_column(Integer, server_default="0")
    revenue_rub: Mapped[Decimal | None] = mapped_column(Money)
    revenue_known_count: Mapped[int] = mapped_column(Integer, server_default="0")
    segment: Mapped[str] = mapped_column(String(16))  # large | mid | small | unknown
    industries: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = _now()


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_client_type_is_valid", "client_type", "is_valid"),
        {"schema": "core"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_valid: Mapped[bool] = mapped_column(Boolean)
    invalid_reason: Mapped[str | None] = mapped_column(String(64))
    source_rows: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    org_type: Mapped[str | None] = mapped_column(Text)
    okfs_name: Mapped[str | None] = mapped_column(Text)
    foreign_ownership: Mapped[bool | None] = mapped_column(Boolean)
    age_bucket: Mapped[str | None] = mapped_column(String(16))
    eco_stage: Mapped[str | None] = mapped_column(Text)
    okved_code: Mapped[str | None] = mapped_column(ForeignKey("core.okved_dict.code"), index=True)
    okved_name: Mapped[str | None] = mapped_column(Text)
    owner_info_reliable: Mapped[bool | None] = mapped_column(Boolean)
    staff_bucket: Mapped[str | None] = mapped_column(String(32))
    staff_bucket_spark: Mapped[str | None] = mapped_column(String(32))
    client_type: Mapped[str | None] = mapped_column(String(16))
    fts_flag_raw: Mapped[bool | None] = mapped_column(Boolean)
    fts_flag: Mapped[bool | None] = mapped_column(Boolean)
    revenue_rub: Mapped[Decimal | None] = mapped_column(Money)
    revenue_stub: Mapped[bool] = mapped_column(Boolean, server_default="false")
    group_id: Mapped[str | None] = mapped_column(ForeignKey("core.company_groups.id"), index=True)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text, index=True)
    segment: Mapped[str | None] = mapped_column(String(16))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = _now()


class CompanyProduct(Base):
    __tablename__ = "company_products"
    __table_args__ = {"schema": "core"}

    company_id: Mapped[str] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), primary_key=True
    )
    product: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)


class CompanyFtsCountry(Base):
    __tablename__ = "company_fts_countries"
    __table_args__ = {"schema": "core"}

    company_id: Mapped[str] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), primary_key=True
    )
    country: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = {"schema": "core"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    n_companies: Mapped[int] = mapped_column(Integer)
    n_known_companies: Mapped[int] = mapped_column(Integer)


class PersonCompanyLink(Base):
    __tablename__ = "person_company_links"
    __table_args__ = {"schema": "core"}

    person_id: Mapped[str] = mapped_column(
        ForeignKey("core.persons.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    company_known: Mapped[bool] = mapped_column(Boolean)
    city_name: Mapped[str | None] = mapped_column(Text)
    rc_name: Mapped[str | None] = mapped_column(Text)


class PaymentAgg(Base):
    """Aggregated payments payer -> beneficiary (one row per pair)."""

    __tablename__ = "payments_agg"
    __table_args__ = (
        UniqueConstraint("payer_id", "beneficiary_id"),
        Index("ix_payments_agg_beneficiary_id", "beneficiary_id"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payer_id: Mapped[str] = mapped_column(String(32))
    beneficiary_id: Mapped[str] = mapped_column(String(32))
    amount_rub: Mapped[Decimal] = mapped_column(Money)
    tx_count: Mapped[int] = mapped_column(Integer)
    source_rows: Mapped[int] = mapped_column(Integer, server_default="1")
    is_self_transfer: Mapped[bool] = mapped_column(Boolean)
    payer_known: Mapped[bool] = mapped_column(Boolean)
    beneficiary_known: Mapped[bool] = mapped_column(Boolean)
    party_invalid: Mapped[bool] = mapped_column(Boolean, server_default="false")


class CompanyEdge(Base):
    """Graph edges between *known valid* companies.

    edge_type: payment (src=payer -> dst=beneficiary), shared_person (src < dst),
    hypothesis (relation guesses with confidence + evidence). Group membership is not
    materialized pairwise (O(n^2) for big groups) — it is companies.group_id (ADR-015).
    """

    __tablename__ = "company_edges"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "edge_type", "label"),
        Index("ix_company_edges_dst_id", "dst_id"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    src_id: Mapped[str] = mapped_column(String(32))
    dst_id: Mapped[str] = mapped_column(String(32))
    edge_type: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(32), server_default="")
    weight: Mapped[float] = mapped_column(Float)
    amount_rub: Mapped[Decimal | None] = mapped_column(Money)
    share_src: Mapped[float | None] = mapped_column(Float)
    share_dst: Mapped[float | None] = mapped_column(Float)
    is_hypothesis: Mapped[bool] = mapped_column(Boolean, server_default="false")
    confidence: Mapped[float] = mapped_column(Float, server_default="1")
    evidence: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class CompanyContext(Base):
    """Materialized personalization profile (features with provenance) + indexed columns for matching."""

    __tablename__ = "company_context"
    __table_args__ = (
        Index("ix_company_context_industry_tags", "industry_tags", postgresql_using="gin"),
        Index("ix_company_context_counterparty_tags_any", "counterparty_tags_any", postgresql_using="gin"),
        Index("ix_company_context_fts_countries", "fts_countries", postgresql_using="gin"),
        Index("ix_company_context_commodities", "commodities", postgresql_using="gin"),
        Index("ix_company_context_products", "products", postgresql_using="gin"),
        {"schema": "core"},
    )

    company_id: Mapped[str] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), primary_key=True
    )
    context_version: Mapped[int] = mapped_column(Integer, server_default="1")
    features: Mapped[dict[str, Any]] = mapped_column()
    completeness: Mapped[dict[str, Any]] = mapped_column()
    user_provided: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    # the scorer's subset of features (values only) — read by matching instead of the full context
    scoring_features: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    # denormalized columns for candidate matching (inverted index)
    client_type: Mapped[str | None] = mapped_column(String(16), index=True)
    okved_section: Mapped[str | None] = mapped_column(String(1), index=True)
    region: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(String(16))
    industry_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    counterparty_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    counterparty_tags_any: Mapped[list[str]] = mapped_column(server_default="{}")  # any share > 0
    fts_countries: Mapped[list[str]] = mapped_column(server_default="{}")
    commodities: Mapped[list[str]] = mapped_column(server_default="{}")
    products: Mapped[list[str]] = mapped_column(server_default="{}")
    rate_sensitive: Mapped[bool] = mapped_column(Boolean, server_default="false")
    fx_sensitive: Mapped[bool] = mapped_column(Boolean, server_default="false")
    completeness_score: Mapped[float] = mapped_column(Float, server_default="0")
    computed_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core: users and access


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    platform_role: Mapped[str] = mapped_column(String(16), server_default="user")  # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = _now()


class UserCompanyAccess(Base):
    __tablename__ = "user_company_access"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("core.companies.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(32))  # owner | ceo | cfo | chief_accountant | treasurer | other
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # opened by the user from the whole client base (demo company picker, ADR-048), not granted by the bank
    added_by_user: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = _now()


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    prefs: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core: news and events


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "core"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # rss | json_api | html | fixture
    url: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, server_default="100")
    fallback_for: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_fixture: Mapped[bool] = mapped_column(Boolean, server_default="false")
    health_status: Mapped[str] = mapped_column(
        String(16), server_default="unknown"
    )  # healthy | degraded | down
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    topics: Mapped[list[str]] = mapped_column(server_default="{}")
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), server_default="catalog")  # catalog | admin (ADR-036)
    disabled_by_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")


class NewsRaw(Base):
    """One fetch of a source: raw response body stored in S3 (news-raw bucket), metadata and outcome here.
    Re-processing reads the body back from S3 without re-downloading (SPEC FR-N2)."""

    __tablename__ = "news_raw"
    __table_args__ = (
        Index("ix_news_raw_source_id_fetched_at", "source_id", "fetched_at"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("core.sources.id"))
    fetched_at: Mapped[datetime] = _now()
    status_code: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(
        String(24)
    )  # ok | not_modified | http_error | network_error | robots_disallowed | parse_error
    s3_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    n_items: Mapped[int] = mapped_column(Integer, server_default="0")
    n_new: Mapped[int] = mapped_column(Integer, server_default="0")
    n_duplicates: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("core.sources.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = _now()
    lang: Mapped[str] = mapped_column(String(8), server_default="ru")
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    flags: Mapped[list[str]] = mapped_column(server_default="{}")
    raw_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.news_raw.id", ondelete="SET NULL"), index=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_industry_tags", "industry_tags", postgresql_using="gin"),
        Index("ix_events_countries", "countries", postgresql_using="gin"),
        Index("ix_events_commodities", "commodities", postgresql_using="gin"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    cluster_key: Mapped[str | None] = mapped_column(Text, unique=True)
    # An event known only from users' links or texts: matched only for their companies until a real source
    # reports it too (ADR-038); NULL = public
    private_company_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8), server_default="none")  # up | down | hold | none
    urgent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    urgency_reasons: Mapped[list[str]] = mapped_column(server_default="{}")
    tags: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    industry_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    countries: Mapped[list[str]] = mapped_column(server_default="{}")
    commodities: Mapped[list[str]] = mapped_column(server_default="{}")
    regions: Mapped[list[str]] = mapped_column(server_default="{}")
    company_ids: Mapped[list[str]] = mapped_column(server_default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_news: Mapped[int] = mapped_column(Integer, server_default="1")


class EventNews(Base):
    __tablename__ = "event_news"
    __table_args__ = {"schema": "core"}

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.events.id", ondelete="CASCADE"), primary_key=True
    )
    news_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.news_items.id", ondelete="CASCADE"), primary_key=True, unique=True
    )


# ---------------------------------------------------------------- core: matching, agent, insights


class CandidateMatch(Base):
    __tablename__ = "candidate_matches"
    __table_args__ = (UniqueConstraint("company_id", "event_id", "role"), {"schema": "core"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("core.companies.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.events.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column()
    matched_features: Mapped[list[str]] = mapped_column(server_default="{}")
    urgent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    context_version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(String(16), server_default="pending", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    scorer: Mapped[str] = mapped_column(String(255), server_default="baseline@1")
    updated_at: Mapped[datetime] = _now()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = {"schema": "core"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # insight | chat
    request_id: Mapped[str] = mapped_column(Text, index=True)
    company_id: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str | None] = mapped_column(String(32))
    generator: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    outcome: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("core.agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_preview: Mapped[str | None] = mapped_column(Text)
    output_preview: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _now()


class ToolAuditLog(Base):
    __tablename__ = "tool_audit_log"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    company_id: Mapped[str | None] = mapped_column(String(32))
    args: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    result_preview: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()


class LlmUsage(Base):
    """Every LLM Gateway call (ADR-028), including refused and cached ones. A `pending` row is the budget
    reservation of a call in flight (estimated cost); it is finalized with the provider's usage."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_day_status", "day", "status"), {"schema": "core"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date)  # budget day (Moscow time)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    caller: Mapped[str] = mapped_column(String(16))  # run | service
    purpose: Mapped[str] = mapped_column(String(16), server_default="other")
    model: Mapped[str] = mapped_column(String(64))  # alias from the allowlist
    provider_model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # pending | ok | cached | error | refused
    stream: Mapped[bool] = mapped_column(Boolean, server_default="false")
    cache_hit: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    estimated_cost_rub: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    cost_rub: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_preview: Mapped[str | None] = mapped_column(Text)  # masked, <= 500 chars
    masked: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCache(Base):
    """Responses to deterministic requests (temperature=0), keyed by a hash of the masked request."""

    __tablename__ = "llm_cache"
    __table_args__ = {"schema": "core"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column()
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    hits: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (
        UniqueConstraint("company_id", "event_id", "role_variant"),
        Index("ix_insights_company_id_relevance_score", "company_id", "relevance_score"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("core.companies.id", ondelete="CASCADE"))
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.events.id", ondelete="CASCADE"), index=True)
    role_variant: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(String(16))
    urgent: Mapped[bool] = mapped_column(Boolean)
    impact_level: Mapped[str] = mapped_column(String(16))
    impact_score: Mapped[int] = mapped_column(Integer)
    relevance_score: Mapped[float] = mapped_column(Float)
    schema_version: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column()
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InsightStatement(Base):
    __tablename__ = "insight_statements"
    __table_args__ = {"schema": "core"}

    insight_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.insights.id", ondelete="CASCADE"), primary_key=True
    )
    statement_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    source_refs: Mapped[list[str]] = mapped_column(server_default="{}")


class InsightSource(Base):
    __tablename__ = "insight_sources"
    __table_args__ = {"schema": "core"}

    insight_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.insights.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    ref_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)


class InsightRejected(Base):
    __tablename__ = "insights_rejected"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64))
    company_id: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[dict[str, Any]] = mapped_column()
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core: user interaction


class UserInsightState(Base):
    """Per user x company x event: hiding survives role switches between insight variants."""

    __tablename__ = "user_insight_state"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    last_insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _now()


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"), index=True)
    insight_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.insights.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(32))
    useful: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class UserAction(Base):
    """User event log for the CJM funnel and the north-star metrics (docs/METRICS.md)."""

    __tablename__ = "user_actions"
    __table_args__ = (Index("ix_user_actions_created_at", "created_at"), {"schema": "core"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(32))
    insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action_type: Mapped[str] = mapped_column(String(32), index=True)  # source_click | cta | role_switch | ...
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()


class BankProduct(Base):
    __tablename__ = "bank_products"
    __table_args__ = {"schema": "core"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(server_default="{}")
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)


class CrmRequest(Base):
    """Mock CRM: requests are only recorded (the bank already has CRM integrations, FAQ #51)."""

    __tablename__ = "crm_requests"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(32), index=True)
    insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_type: Mapped[str] = mapped_column(String(32))  # contact_manager | callback | application
    product_id: Mapped[str | None] = mapped_column(ForeignKey("core.bank_products.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    preferred_time: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), server_default="new"
    )  # new | in_progress | done | rejected
    external_ref: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """In-app notifications (SSE + list): urgent insights, digests, request status, feedback applied."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key"),
        Index("ix_notifications_user_id_id", "user_id", "id"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"))
    company_id: Mapped[str | None] = mapped_column(String(32))
    insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(
        String(24)
    )  # urgent_insight | digest | request_update | feedback_applied
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    dedup_key: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = _now()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QaMessage(Base):
    """Questions to the analytics and the agent's answers (CJM stage 06), per user x company x insight."""

    __tablename__ = "qa_messages"
    __table_args__ = (Index("ix_qa_messages_user_id_company_id", "user_id", "company_id"), {"schema": "core"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"))
    company_id: Mapped[str] = mapped_column(String(32))
    insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    intent: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    statements: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default="ok")  # ok | error
    created_at: Mapped[datetime] = _now()


class MetricsDaily(Base):
    """Daily snapshot of the quality metrics (docs/METRICS.md), filled by the scheduler."""

    __tablename__ = "metrics_daily"
    __table_args__ = {"schema": "core"}

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metrics: Mapped[dict[str, Any]] = mapped_column()
    computed_at: Mapped[datetime] = _now()


class MarketRate(Base):
    """Official market indicators of the Bank of Russia: key rate and exchange rates (ADR-043)."""

    __tablename__ = "market_rates"
    __table_args__ = {"schema": "core"}

    indicator: Mapped[str] = mapped_column(String(32), primary_key=True)  # key_rate | usd_rub | ...
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    fetched_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core: bank analytics


class AnalyticsDocument(Base):
    __tablename__ = "analytics_documents"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    file_name: Mapped[str] = mapped_column(Text)
    s3_key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    doc_date: Mapped[date | None] = mapped_column(Date, index=True)
    topics: Mapped[list[str]] = mapped_column(server_default="{}")
    origin: Mapped[str] = mapped_column(String(16))  # organizers | synthetic | upload
    pages: Mapped[int] = mapped_column(Integer, server_default="0")
    extraction: Mapped[str | None] = mapped_column(
        String(24)
    )  # text_layer | ocr_tesseract | mixed | ocr_fake
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(16), server_default="processing"
    )  # processing | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalyticsPage(Base):
    __tablename__ = "analytics_pages"
    __table_args__ = {"schema": "core"}

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.analytics_documents.id", ondelete="CASCADE"), primary_key=True
    )
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)
    chars: Mapped[int] = mapped_column(Integer)


class AnalyticsChunk(Base):
    __tablename__ = "analytics_chunks"
    __table_args__ = (
        Index("ix_analytics_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_analytics_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_analytics_chunks_document_id_page", "document_id", "page"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.analytics_documents.id", ondelete="CASCADE")
    )
    page: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('russian'::regconfig, coalesce(section, '') || ' ' || text)", persisted=True),
    )
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = _now()
