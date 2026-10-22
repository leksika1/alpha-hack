"""SQLAlchemy ORM models. Migrations are generated from this metadata (alembic --autogenerate) and
reviewed by hand; tests/integration/test_schema.py checks there is no drift.

Schemas: ``staging`` — raw CSV rows as text (COPY target); ``core`` — cleaned, typed data.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(20, 2)


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
    # denormalized columns for candidate matching (inverted index)
    client_type: Mapped[str | None] = mapped_column(String(16), index=True)
    okved_section: Mapped[str | None] = mapped_column(String(1), index=True)
    region: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(String(16))
    industry_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    counterparty_tags: Mapped[list[str]] = mapped_column(server_default="{}")
    fts_countries: Mapped[list[str]] = mapped_column(server_default="{}")
    commodities: Mapped[list[str]] = mapped_column(server_default="{}")
    products: Mapped[list[str]] = mapped_column(server_default="{}")
    rate_sensitive: Mapped[bool] = mapped_column(Boolean, server_default="false")
    fx_sensitive: Mapped[bool] = mapped_column(Boolean, server_default="false")
    completeness_score: Mapped[float] = mapped_column(Float, server_default="0")
    computed_at: Mapped[datetime] = _now()
