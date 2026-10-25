"""Companies ingest: CSV -> staging (COPY) -> cleaning -> core -> graph -> context -> quality report.

Idempotent: a re-run upserts companies (keeps user data and FKs), reloads derived tables and
bumps context versions. Companies absent from the latest load are kept but marked invalid.
"""

import csv
import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domain.cleaning import (
    RC_NAME_GARBAGE,
    CleanCompany,
    clean_company_rows,
    segment_by_revenue,
)
from app.domain.okved import OkvedEntry, OkvedReference, classify, load_reference
from app.services.company_context import rebuild_contexts

log = get_logger(__name__)

COMPANIES_FILE = "df_Uid_data_hack.csv"
PAYMENTS_FILE = "df_Uid_payment_hack.csv"
LINKS_FILE = "df_Fid_Uid_link_hack.csv"

COMPANY_COLUMNS = [
    "SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL",
    "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK", "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK", "REVENUE_COMPANY", "GROUP_ID", "CITY", "REGION",
]  # fmt: skip
PAYMENT_COLUMNS = ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]
LINK_COLUMNS = ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"]

SUPPLIER_SECTIONS = ["A", "B", "C", "D", "G", "H"]
KEY_SHARE = 0.10
NUMBER_RE = r"^\s*-?[0-9]+(\.[0-9]+)?\s*$"


class IngestError(RuntimeError):
    pass


@dataclass
class QualityCheck:
    dataset: str
    check_name: str
    severity: str
    value: float | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestResult:
    run_id: uuid.UUID
    stats: dict[str, Any]
    checks: list[QualityCheck]


def _check_header(path: Path, expected: list[str]) -> None:
    if not path.exists():
        raise IngestError(f"file not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh, delimiter=";"))
    if [h.strip().upper() for h in header] != expected:
        raise IngestError(f"unexpected header in {path.name}: {header}")


async def _copy_csv(conn: Any, path: Path, table: str, columns: list[str]) -> int:
    await conn.execute(f"TRUNCATE staging.{table} RESTART IDENTITY")
    await conn.copy_to_table(
        table,
        schema_name="staging",
        source=str(path),
        columns=[c.lower() for c in columns],
        format="csv",
        delimiter=";",
        header=True,
        quote='"',
        encoding="UTF8",
    )
    # table names are module constants, never user input
    return int(await conn.fetchval(f"SELECT count(*) FROM staging.{table}"))  # noqa: S608


# ------------------------------------------------------------------ companies


async def _load_okved(conn: Any, ref: OkvedReference) -> dict[str, OkvedEntry]:
    rows = await conn.fetch(
        """SELECT mainokved_code AS code, min(mainokved_name) AS name
           FROM staging.companies_raw WHERE coalesce(trim(mainokved_code), '') <> ''
           GROUP BY mainokved_code"""
    )
    entries: dict[str, OkvedEntry] = {}
    for r in rows:
        e = classify(r["code"], r["name"], ref)
        if e.code:
            entries[r["code"]] = e
    await conn.executemany(
        """INSERT INTO core.okved_dict (code, name, class_code, group_code, section, section_name,
               industry_tags, commodities, consumer_demand, import_dependent, export_oriented,
               rate_sensitive_industry, known_class)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
           ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, class_code=EXCLUDED.class_code,
               group_code=EXCLUDED.group_code, section=EXCLUDED.section, section_name=EXCLUDED.section_name,
               industry_tags=EXCLUDED.industry_tags, commodities=EXCLUDED.commodities,
               consumer_demand=EXCLUDED.consumer_demand, import_dependent=EXCLUDED.import_dependent,
               export_oriented=EXCLUDED.export_oriented,
               rate_sensitive_industry=EXCLUDED.rate_sensitive_industry, known_class=EXCLUDED.known_class""",
        [
            (
                e.code, e.name, e.class_code, e.group_code, e.section, e.section_name, e.industry_tags,
                e.commodities, e.flags.get("consumer_demand", False), e.flags.get("import_dependent", False),
                e.flags.get("export_oriented", False), e.flags.get("rate_sensitive_industry", False), e.known,
            )
            for e in {x.code: x for x in entries.values()}.values()
        ],
    )  # fmt: skip
    return entries


def _group_rows(companies: list[CleanCompany], okved: dict[str, OkvedEntry]) -> list[tuple[Any, ...]]:
    by_group: dict[str, list[CleanCompany]] = defaultdict(list)
    for c in companies:
        if c.is_valid and c.group_id:
            by_group[c.group_id].append(c)
    section_of = {e.code: e.section for e in okved.values()}
    rows = []
    for gid, members in by_group.items():
        revenues = [m.revenue_rub for m in members if m.revenue_rub is not None]
        revenue = sum(revenues, Decimal(0)) if revenues else None
        industries = Counter(section_of.get(m.okved_code or "") or "?" for m in members)
        rows.append(
            (
                gid,
                len(members),
                len(members),
                sum(1 for m in members if m.client_type == "CLIENT"),
                revenue,
                len(revenues),
                segment_by_revenue(revenue),
                json.dumps(dict(industries), ensure_ascii=False),
            )
        )
    return rows


async def _load_companies(conn: Any, okved: dict[str, OkvedEntry], run_id: uuid.UUID) -> list[CleanCompany]:
    raw = await conn.fetch("SELECT * FROM staging.companies_raw ORDER BY row_num")
    rows_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in raw:
        cid = (r["synthetic_u"] or "").strip()
        if cid:
            rows_by_id[cid].append(dict(r))
    companies: list[CleanCompany] = []
    for cid, rows in rows_by_id.items():
        code_raw = rows[0].get("mainokved_code")
        entry = okved.get(code_raw) if code_raw else None
        companies.append(clean_company_rows(cid, rows, entry.code if entry else None))

    groups = _group_rows(companies, okved)
    await conn.execute(
        "CREATE TEMP TABLE tmp_groups (id text, size int, valid_size int, client_count int,"
        " revenue_rub numeric, revenue_known_count int, segment text, industries text) ON COMMIT DROP"
    )
    await conn.copy_records_to_table("tmp_groups", records=groups)
    await conn.execute(
        """INSERT INTO core.company_groups (id, size, valid_size, client_count, revenue_rub,
               revenue_known_count, segment, industries)
           SELECT id, size, valid_size, client_count, revenue_rub, revenue_known_count, segment,
                  industries::jsonb
           FROM tmp_groups
           ON CONFLICT (id) DO UPDATE SET size=EXCLUDED.size, valid_size=EXCLUDED.valid_size,
               client_count=EXCLUDED.client_count, revenue_rub=EXCLUDED.revenue_rub,
               revenue_known_count=EXCLUDED.revenue_known_count, segment=EXCLUDED.segment,
               industries=EXCLUDED.industries, updated_at=now()"""
    )
    group_segment = {g[0]: g[6] for g in groups}

    cols = [
        "id", "is_valid", "invalid_reason", "source_rows", "org_type", "okfs_name", "foreign_ownership",
        "age_bucket", "eco_stage", "okved_code", "okved_name", "owner_info_reliable", "staff_bucket",
        "staff_bucket_spark", "client_type", "fts_flag_raw", "fts_flag", "revenue_rub", "revenue_stub",
        "group_id", "city", "region", "segment", "run_id",
    ]  # fmt: skip
    records = [
        (
            c.id, c.is_valid, c.invalid_reason, c.source_rows, c.org_type, c.okfs_name, c.foreign_ownership,
            c.age_bucket, c.eco_stage, c.okved_code, c.okved_name, c.owner_info_reliable, c.staff_bucket,
            c.staff_bucket_spark, c.client_type, c.fts_flag_raw, c.fts_flag, c.revenue_rub, c.revenue_stub,
            c.group_id, c.city, c.region,
            group_segment.get(c.group_id or "", segment_by_revenue(c.revenue_rub)) if c.is_valid else None,
            run_id,
        )
        for c in companies
    ]  # fmt: skip
    # column lists below are module constants, never user input
    create_tmp = "CREATE TEMP TABLE tmp_companies AS SELECT " + ", ".join(cols)
    await conn.execute(create_tmp + " FROM core.companies WITH NO DATA")
    await conn.copy_records_to_table("tmp_companies", records=records, columns=cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
    await conn.execute(
        f"INSERT INTO core.companies ({', '.join(cols)}) SELECT {', '.join(cols)} FROM tmp_companies "  # noqa: S608
        f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at=now()"
    )
    await conn.execute("DROP TABLE tmp_companies")
    await conn.execute(
        """UPDATE core.companies
           SET is_valid=false, invalid_reason='absent_in_latest_load', updated_at=now()
           WHERE run_id IS DISTINCT FROM $1""",
        run_id,
    )
    await conn.execute(
        "DELETE FROM core.company_groups g"
        " WHERE NOT EXISTS (SELECT 1 FROM core.companies c WHERE c.group_id = g.id)"
    )

    await conn.execute("TRUNCATE core.company_products, core.company_fts_countries")
    await conn.copy_records_to_table(
        "company_products",
        schema_name="core",
        records=[(c.id, p) for c in companies if c.is_valid for p in c.products],
        columns=["company_id", "product"],
    )
    await conn.copy_records_to_table(
        "company_fts_countries",
        schema_name="core",
        records=[(c.id, k) for c in companies if c.is_valid for k in c.countries],
        columns=["company_id", "country"],
    )
    return companies


# ------------------------------------------------------------------ payments, persons, graph


async def _load_payments(conn: Any) -> None:
    await conn.execute("TRUNCATE core.payments_agg RESTART IDENTITY")
    # NUMBER_RE is a module constant
    await conn.execute(
        f"""INSERT INTO core.payments_agg (payer_id, beneficiary_id, amount_rub, tx_count, source_rows,
                is_self_transfer, payer_known, beneficiary_known, party_invalid)
            SELECT p.payer, p.beneficiary, sum(p.amt), sum(p.cnt), count(*), p.payer = p.beneficiary,
                   coalesce(bool_or(cp.is_valid), false), coalesce(bool_or(cb.is_valid), false),
                   coalesce(bool_or(cp.is_valid = false OR cb.is_valid = false), false)
            FROM (SELECT trim(payer) AS payer, trim(beneficiary) AS beneficiary,
                         pay_amt::numeric AS amt, pay_cnt::numeric::int AS cnt
                  FROM staging.payments_raw
                  WHERE coalesce(trim(payer), '') <> '' AND coalesce(trim(beneficiary), '') <> ''
                    AND pay_amt ~ '{NUMBER_RE}' AND pay_cnt ~ '{NUMBER_RE}') p
            LEFT JOIN core.companies cp ON cp.id = p.payer
            LEFT JOIN core.companies cb ON cb.id = p.beneficiary
            GROUP BY p.payer, p.beneficiary"""  # noqa: S608
    )


async def _load_persons(conn: Any) -> None:
    await conn.execute("TRUNCATE core.person_company_links, core.persons")
    await conn.execute(
        """INSERT INTO core.persons (id, n_companies, n_known_companies)
           SELECT l.f, count(DISTINCT l.u), count(DISTINCT l.u) FILTER (WHERE c.is_valid)
           FROM (SELECT trim(synthetic_f) AS f, trim(synthetic_u) AS u FROM staging.person_links_raw) l
           LEFT JOIN core.companies c ON c.id = l.u
           WHERE coalesce(l.f, '') <> '' AND coalesce(l.u, '') <> ''
           GROUP BY l.f"""
    )
    await conn.execute(
        """INSERT INTO core.person_company_links (person_id, company_id, company_known, city_name, rc_name)
           SELECT DISTINCT ON (l.f, l.u) l.f, l.u, coalesce(c.is_valid, false),
                  nullif(trim(l.city_name), ''),
                  CASE WHEN lower(trim(coalesce(l.rc_name, ''))) = ANY($1::text[]) THEN NULL
                       ELSE trim(l.rc_name) END
           FROM (SELECT trim(synthetic_f) AS f, trim(synthetic_u) AS u, city_name, rc_name, row_num
                 FROM staging.person_links_raw) l
           LEFT JOIN core.companies c ON c.id = l.u
           WHERE coalesce(l.f, '') <> '' AND coalesce(l.u, '') <> ''
           ORDER BY l.f, l.u, l.row_num""",
        sorted(RC_NAME_GARBAGE),
    )


async def _build_edges(conn: Any) -> None:
    await conn.execute("TRUNCATE core.company_edges RESTART IDENTITY")
    # Totals include unknown counterparties so that shares are shares of the whole turnover.
    await conn.execute(
        """INSERT INTO core.company_edges (src_id, dst_id, edge_type, label, weight, amount_rub, share_src,
                                          share_dst, evidence)
           WITH out_tot AS (SELECT payer_id AS id, sum(amount_rub) AS tot FROM core.payments_agg
                            WHERE NOT is_self_transfer AND payer_known GROUP BY payer_id),
                in_tot AS (SELECT beneficiary_id AS id, sum(amount_rub) AS tot FROM core.payments_agg
                           WHERE NOT is_self_transfer AND beneficiary_known GROUP BY beneficiary_id)
           SELECT p.payer_id, p.beneficiary_id, 'payment', '', p.amount_rub::float8, p.amount_rub,
                  (p.amount_rub / nullif(o.tot, 0))::float8, (p.amount_rub / nullif(i.tot, 0))::float8,
                  jsonb_build_object('tx_count', p.tx_count)
           FROM core.payments_agg p
           JOIN out_tot o ON o.id = p.payer_id
           JOIN in_tot i ON i.id = p.beneficiary_id
           WHERE NOT p.is_self_transfer AND p.payer_known AND p.beneficiary_known"""
    )
    await conn.execute(
        """INSERT INTO core.company_edges (src_id, dst_id, edge_type, label, weight, evidence)
           SELECT a.company_id, b.company_id, 'shared_person', '', count(*)::float8,
                  jsonb_build_object('n_shared_persons', count(*))
           FROM core.person_company_links a
           JOIN core.person_company_links b ON a.person_id = b.person_id AND a.company_id < b.company_id
           WHERE a.company_known AND b.company_known
           GROUP BY a.company_id, b.company_id"""
    )
    # Hypotheses (FAQ #23, #30): payments show a financial link, not its type. Named as hypotheses
    # with confidence and evidence.
    await conn.execute(
        """INSERT INTO core.company_edges (src_id, dst_id, edge_type, label, weight, amount_rub, share_src,
                                          share_dst, is_hypothesis, confidence, evidence)
           SELECT e.src_id, e.dst_id, 'hypothesis', 'key_supplier', e.share_src, e.amount_rub, e.share_src,
                  e.share_dst, true, least(0.85, 0.35 + e.share_src),
                  jsonb_build_object(
                    'statement', 'получатель — вероятный ключевой поставщик плательщика',
                    'basis', jsonb_build_array('доля в исходящих платежах плательщика',
                                               'отрасль получателя производит/поставляет товары'),
                    'payment_share_of_src_outflow', round(e.share_src::numeric, 4),
                    'src_okved', cs.okved_code, 'dst_okved', cd.okved_code)
           FROM core.company_edges e
           JOIN core.companies cs ON cs.id = e.src_id
           JOIN core.companies cd ON cd.id = e.dst_id
           JOIN core.okved_dict od ON od.code = cd.okved_code
           WHERE e.edge_type = 'payment' AND e.share_src >= $1 AND od.section = ANY($2::text[])""",
        KEY_SHARE,
        SUPPLIER_SECTIONS,
    )
    await conn.execute(
        """INSERT INTO core.company_edges (src_id, dst_id, edge_type, label, weight, amount_rub, share_src,
                                          share_dst, is_hypothesis, confidence, evidence)
           SELECT e.src_id, e.dst_id, 'hypothesis', 'key_customer', e.share_dst, e.amount_rub, e.share_src,
                  e.share_dst, true, least(0.85, 0.35 + e.share_dst),
                  jsonb_build_object(
                    'statement', 'плательщик — вероятный ключевой покупатель получателя',
                    'basis', jsonb_build_array('доля во входящих платежах получателя'),
                    'payment_share_of_dst_inflow', round(e.share_dst::numeric, 4))
           FROM core.company_edges e
           WHERE e.edge_type = 'payment' AND e.share_dst >= $1""",
        KEY_SHARE,
    )
    await conn.execute(
        """INSERT INTO core.company_edges (src_id, dst_id, edge_type, label, weight, is_hypothesis,
                                          confidence, evidence)
           SELECT s.src_id, s.dst_id, 'hypothesis', 'affiliated_outside_group', s.weight, true,
                  CASE WHEN pay.has_payment THEN 0.6 ELSE 0.4 END,
                  jsonb_build_object(
                    'statement', 'возможная аффилированность вне формальной группы',
                    'basis', jsonb_build_array('общие физлица', 'разные GROUP_ID')
                             || CASE WHEN pay.has_payment THEN '["есть платежи между компаниями"]'::jsonb
                                     ELSE '[]'::jsonb END,
                    'n_shared_persons', s.weight)
           FROM core.company_edges s
           JOIN core.companies a ON a.id = s.src_id
           JOIN core.companies b ON b.id = s.dst_id
           CROSS JOIN LATERAL (SELECT EXISTS (
               SELECT 1 FROM core.company_edges p WHERE p.edge_type = 'payment'
                 AND ((p.src_id = s.src_id AND p.dst_id = s.dst_id)
                      OR (p.src_id = s.dst_id AND p.dst_id = s.src_id))
           ) AS has_payment) pay
           WHERE s.edge_type = 'shared_person' AND a.group_id IS DISTINCT FROM b.group_id"""
    )


# ------------------------------------------------------------------ data quality


async def _quality_checks(conn: Any, companies: list[CleanCompany]) -> list[QualityCheck]:
    checks: list[QualityCheck] = []

    def add(ds: str, name: str, value: float | None, severity: str = "info", **details: Any) -> None:
        checks.append(QualityCheck(ds, name, severity, value, details))

    raw_rows = int(await conn.fetchval("SELECT count(*) FROM staging.companies_raw"))
    dups = [c for c in companies if c.invalid_reason == "pseudo_id_collision"]
    valid = [c for c in companies if c.is_valid]
    add("companies", "rows_total", raw_rows)
    add("companies", "distinct_ids", len(companies))
    add("companies", "duplicate_id_rows", sum(c.source_rows for c in dups), "warn" if dups else "info",
        ids={c.id: c.source_rows for c in dups},
        rule="маркер is_valid=false, строки не схлопываются (ADR-009)")  # fmt: skip
    add("companies", "revenue_stub_rows", sum(1 for c in valid if c.revenue_stub), "warn",
        rule="REVENUE_COMPANY=1000 -> NULL")  # fmt: skip
    for name in ("eco_stage", "staff_bucket_spark", "region", "okved_code", "group_id"):
        missing = sum(1 for c in valid if getattr(c, name) is None)
        add("companies", f"missing_{name}", missing, "warn" if missing else "info",
            share=round(missing / max(len(valid), 1), 4))  # fmt: skip
    issues = Counter(i for c in valid for i in c.issues)
    for issue in ("fts_flag_0_with_countries", "fts_flag_1_without_countries"):
        add("companies", issue, issues.get(issue, 0), "warn" if issues.get(issue) else "info",
            rule="fts_flag = FTS_FLAG OR есть страны")  # fmt: skip
    add("companies", "org_type_distinct", len({c.org_type for c in valid}), "info",
        note="ORG_TYPE константен -> не используется как признак")  # fmt: skip
    add("companies", "client_types", len(valid), "info", **dict(Counter(c.client_type or "?" for c in valid)))
    unknown_class = int(await conn.fetchval("SELECT count(*) FROM core.okved_dict WHERE NOT known_class"))
    add("companies", "okved_unknown_class", unknown_class, "warn" if unknown_class else "info")

    p = await conn.fetchrow(
        f"""SELECT count(*) AS rows_total,
                   count(*) FILTER (WHERE NOT (pay_amt ~ '{NUMBER_RE}' AND pay_cnt ~ '{NUMBER_RE}'))
                       AS rejected,
                   count(*) FILTER (WHERE trim(payer) = trim(beneficiary)) AS self_rows,
                   count(*) FILTER (WHERE cp.id IS NULL) AS payer_unknown,
                   count(*) FILTER (WHERE cb.id IS NULL) AS beneficiary_unknown
            FROM staging.payments_raw r
            LEFT JOIN core.companies cp ON cp.id = trim(r.payer) AND cp.is_valid
            LEFT JOIN core.companies cb ON cb.id = trim(r.beneficiary) AND cb.is_valid"""  # noqa: S608
    )
    agg = await conn.fetchrow(
        """SELECT coalesce(sum(amount_rub) FILTER (WHERE is_self_transfer) / nullif(sum(amount_rub), 0), 0)
                      AS self_share,
                  count(*) FILTER (WHERE source_rows > 1) AS dup_pairs,
                  count(*) FILTER (WHERE party_invalid) AS invalid_party
           FROM core.payments_agg"""
    )
    total = max(int(p["rows_total"]), 1)
    add("payments", "rows_total", p["rows_total"])
    add("payments", "rejected_rows", p["rejected"], "error" if p["rejected"] else "info")
    add("payments", "self_transfer_rows", p["self_rows"], "info",
        amount_share=round(float(agg["self_share"]), 4),
        rule="исключаются из анализа контрагентов")  # fmt: skip
    add("payments", "payer_unknown_rows", p["payer_unknown"], "warn",
        share=round(p["payer_unknown"] / total, 4),
        rule="неизвестный контрагент, профиль не восстанавливается")  # fmt: skip
    add("payments", "beneficiary_unknown_rows", p["beneficiary_unknown"], "warn",
        share=round(p["beneficiary_unknown"] / total, 4))  # fmt: skip
    add("payments", "duplicate_pairs", agg["dup_pairs"], "warn" if agg["dup_pairs"] else "info",
        rule="агрегируются суммой")  # fmt: skip
    add("payments", "invalid_party_pairs", agg["invalid_party"], "warn" if agg["invalid_party"] else "info")

    lk = await conn.fetchrow(
        """SELECT count(*) AS rows_total, count(DISTINCT trim(synthetic_f)) AS persons,
                  count(DISTINCT trim(synthetic_u)) AS companies,
                  count(DISTINCT trim(synthetic_u)) FILTER (WHERE c.id IS NULL) AS companies_unknown,
                  count(*) FILTER (WHERE lower(trim(coalesce(rc_name, ''))) = ANY($1::text[])) AS rc_garbage
           FROM staging.person_links_raw l
           LEFT JOIN core.companies c ON c.id = trim(l.synthetic_u) AND c.is_valid""",
        sorted(RC_NAME_GARBAGE),
    )
    add("person_links", "rows_total", lk["rows_total"])
    add("person_links", "persons", lk["persons"])
    add("person_links", "companies_unknown", lk["companies_unknown"], "warn",
        share=round(lk["companies_unknown"] / max(int(lk["companies"]), 1), 4),
        rule="связь сохраняется, рёбер графа не даёт")  # fmt: skip
    add("person_links", "rc_name_garbage_rows", lk["rc_garbage"], "warn" if lk["rc_garbage"] else "info",
        rule="-> NULL")  # fmt: skip
    return checks


# ------------------------------------------------------------------ orchestration


async def ingest_companies(conn: Any, data_dir: Path, reference_dir: Path) -> IngestResult:
    started = time.perf_counter()
    paths = {name: data_dir / name for name in (COMPANIES_FILE, PAYMENTS_FILE, LINKS_FILE)}
    _check_header(paths[COMPANIES_FILE], COMPANY_COLUMNS)
    _check_header(paths[PAYMENTS_FILE], PAYMENT_COLUMNS)
    _check_header(paths[LINKS_FILE], LINK_COLUMNS)
    ref = load_reference(reference_dir)

    run_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO core.ingest_runs (id, kind, source) VALUES ($1, 'companies', $2)", run_id, str(data_dir)
    )
    stats: dict[str, Any] = {}
    timings: dict[str, float] = {}

    def lap(name: str, t0: float) -> float:
        timings[name] = round(time.perf_counter() - t0, 2)
        log.info("ingest_step", step=name, seconds=timings[name])
        return time.perf_counter()

    try:
        t = time.perf_counter()
        stats["staging_companies"] = await _copy_csv(
            conn, paths[COMPANIES_FILE], "companies_raw", COMPANY_COLUMNS
        )
        stats["staging_payments"] = await _copy_csv(
            conn, paths[PAYMENTS_FILE], "payments_raw", PAYMENT_COLUMNS
        )
        stats["staging_links"] = await _copy_csv(conn, paths[LINKS_FILE], "person_links_raw", LINK_COLUMNS)
        t = lap("copy_staging", t)
        async with conn.transaction():
            okved = await _load_okved(conn, ref)
            companies = await _load_companies(conn, okved, run_id)
            t = lap("companies", t)
            await _load_payments(conn)
            t = lap("payments", t)
            await _load_persons(conn)
            t = lap("persons", t)
            await _build_edges(conn)
            t = lap("edges", t)
        ctx_stats = await rebuild_contexts(conn, None)
        t = lap("context", t)
        checks = await _quality_checks(conn, companies)
        await conn.executemany(
            """INSERT INTO core.data_quality_report (run_id, dataset, check_name, severity, value, details)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            [
                (
                    run_id,
                    c.dataset,
                    c.check_name,
                    c.severity,
                    c.value,
                    json.dumps(c.details, ensure_ascii=False),
                )
                for c in checks
            ],
        )
        lap("quality", t)
        counts = await conn.fetchrow(
            """SELECT (SELECT count(*) FROM core.companies WHERE is_valid) AS companies_valid,
                      (SELECT count(*) FROM core.companies WHERE NOT is_valid) AS companies_invalid,
                      (SELECT count(*) FROM core.company_groups) AS groups,
                      (SELECT count(*) FROM core.payments_agg) AS payment_pairs,
                      (SELECT count(*) FROM core.persons) AS persons,
                      (SELECT count(*) FROM core.company_edges) AS edges"""
        )
        stats.update(dict(counts))
        stats["contexts"] = ctx_stats
        stats["timings_s"] = timings
        stats["total_s"] = round(time.perf_counter() - started, 2)
        await conn.execute(
            "UPDATE core.ingest_runs SET status='ok', stats=$2::jsonb, finished_at=now() WHERE id=$1",
            run_id,
            json.dumps(stats, ensure_ascii=False, default=str),
        )
    except Exception as exc:
        await conn.execute(
            "UPDATE core.ingest_runs SET status='failed', error=$2, finished_at=now() WHERE id=$1",
            run_id,
            repr(exc)[:2000],
        )
        raise
    log.info(
        "ingest_companies_done", run_id=str(run_id), **{k: v for k, v in stats.items() if k != "timings_s"}
    )
    return IngestResult(run_id=run_id, stats=stats, checks=checks)
