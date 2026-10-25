"""Cleaning rules for the organizers' CSVs (docs/DATA.md). Pure functions, no I/O."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.domain.regions import normalize_city, normalize_region

REVENUE_STUB_RUB = Decimal(1000)  # technical placeholder (FAQ #15) -> NULL
LARGE_BUSINESS_RUB = Decimal(15_000_000_000)  # case: large business = group revenue > 15 bn RUB
MID_BUSINESS_RUB = Decimal(800_000_000)  # lower bound of mid-size by revenue (ADR-016)

RC_NAME_GARBAGE = {"", "инн не определён", "инн не определен", "не определено"}

PRODUCT_COLUMNS: dict[str, str] = {
    "cl_product_rko_mark": "rko",
    "cl_product_gar_mark": "guarantee",
    "cl_product_loa_mark": "loan",
    "cl_product_lea_mark": "leasing",
    "cl_product_dep_mark": "deposit",
    "cl_product_zpp_mark": "payroll",
    "factoring_mark": "factoring",
}
ALL_PRODUCTS = tuple(PRODUCT_COLUMNS.values())


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_flag(value: str | None) -> bool | None:
    """'1.0' / '1' / 'true' -> True, '0.0' / '0' -> False, '' -> None."""
    v = blank_to_none(value)
    if v is None:
        return None
    v = v.lower()
    if v in {"1", "1.0", "true", "t", "yes"}:
        return True
    if v in {"0", "0.0", "false", "f", "no"}:
        return False
    return None


def parse_decimal(value: str | None) -> Decimal | None:
    v = blank_to_none(value)
    if v is None:
        return None
    try:
        d = Decimal(v.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def parse_revenue(value: str | None) -> tuple[Decimal | None, bool]:
    """Returns (revenue_rub, is_stub). The 1000 RUB placeholder becomes (None, True)."""
    d = parse_decimal(value)
    if d is None:
        return None, False
    if d == REVENUE_STUB_RUB:
        return None, True
    if d < 0:
        return None, False
    return d.quantize(Decimal("0.01")), False


def parse_countries(value: str | None) -> list[str]:
    """'ОАЭ, ИНДИЯ, КИТАЙ' -> ['ИНДИЯ', 'КИТАЙ', 'ОАЭ'] (unique, upper-case, sorted)."""
    v = blank_to_none(value)
    if v is None:
        return []
    return sorted({c.strip().upper() for c in v.split(",") if c.strip()})


def clean_rc_name(value: str | None) -> str | None:
    v = blank_to_none(value)
    if v is None or v.lower() in RC_NAME_GARBAGE:
        return None
    return v


def is_foreign_ownership(okfs_name: str | None) -> bool | None:
    v = blank_to_none(okfs_name)
    if v is None:
        return None
    return "ИНОСТРАН" in v.upper()


def segment_by_revenue(revenue: Decimal | None) -> str:
    if revenue is None:
        return "unknown"
    if revenue > LARGE_BUSINESS_RUB:
        return "large"
    if revenue >= MID_BUSINESS_RUB:
        return "mid"
    return "small"


@dataclass(slots=True)
class CleanCompany:
    id: str
    is_valid: bool
    invalid_reason: str | None
    source_rows: int
    org_type: str | None = None
    okfs_name: str | None = None
    foreign_ownership: bool | None = None
    age_bucket: str | None = None
    eco_stage: str | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    owner_info_reliable: bool | None = None
    staff_bucket: str | None = None
    staff_bucket_spark: str | None = None
    client_type: str | None = None
    fts_flag_raw: bool | None = None
    fts_flag: bool | None = None
    revenue_rub: Decimal | None = None
    revenue_stub: bool = False
    group_id: str | None = None
    city: str | None = None
    region: str | None = None
    products: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def clean_company_rows(
    company_id: str, rows: list[dict[str, str | None]], okved_code: str | None
) -> CleanCompany:
    """One CSV id -> one core company. Several rows with the same id (anonymization artifact,
    FAQ #17) produce an invalid marker without business fields — the rows are not merged."""
    if len(rows) > 1:
        return CleanCompany(
            id=company_id, is_valid=False, invalid_reason="pseudo_id_collision", source_rows=len(rows)
        )
    r = rows[0]
    revenue, stub = parse_revenue(r.get("revenue_company"))
    countries = parse_countries(r.get("fts_country_name"))
    fts_raw = parse_flag(r.get("fts_flag"))
    issues: list[str] = []
    if fts_raw is False and countries:
        issues.append("fts_flag_0_with_countries")
    if fts_raw is True and not countries:
        issues.append("fts_flag_1_without_countries")
    products = [name for col, name in PRODUCT_COLUMNS.items() if parse_flag(r.get(col))]
    return CleanCompany(
        id=company_id,
        is_valid=True,
        invalid_reason=None,
        source_rows=1,
        org_type=blank_to_none(r.get("org_type")),
        okfs_name=blank_to_none(r.get("okfs_name")),
        foreign_ownership=is_foreign_ownership(r.get("okfs_name")),
        age_bucket=blank_to_none(r.get("company_age_years_gradation")),
        eco_stage=blank_to_none(r.get("eco_mark_label")),
        okved_code=okved_code,
        okved_name=blank_to_none(r.get("mainokved_name")),
        owner_info_reliable=parse_flag(r.get("negativemark")),
        staff_bucket=blank_to_none(r.get("amount_workers_fns_gradation")),
        staff_bucket_spark=blank_to_none(r.get("amount_workers_spark_gradation")),
        client_type=blank_to_none(r.get("cl_type")),
        fts_flag_raw=fts_raw,
        fts_flag=bool(fts_raw) or bool(countries),
        revenue_rub=revenue,
        revenue_stub=stub,
        group_id=blank_to_none(r.get("group_id")),
        city=normalize_city(blank_to_none(r.get("city"))),
        region=normalize_region(blank_to_none(r.get("region"))),
        products=products,
        countries=countries,
        issues=issues,
    )
