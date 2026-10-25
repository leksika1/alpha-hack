"""Companies ingest on real Postgres+pgvector with the committed synthetic dataset."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.pg import pg_connection
from app.services.company_context import rebuild_contexts
from app.services.demo_selection import select_demo_companies
from app.services.ingest_companies import IngestError, ingest_companies
from app.services.synthetic import DEMO, PSEUDO_ID

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SYNTH = ROOT / "data" / "synthetic"
REF = ROOT / "data" / "reference"


@pytest.fixture(scope="module")
async def loaded(infra: dict[str, str]) -> dict[str, Any]:
    async with pg_connection(infra["DATABASE_URL"]) as conn:
        # precondition: no user-provided facts (other modules may have filled the questionnaire)
        if await conn.fetchval("SELECT to_regclass('core.company_context') IS NOT NULL"):
            await conn.execute("UPDATE core.company_context SET user_provided = '{}'::jsonb")
        result = await ingest_companies(conn, SYNTH, REF)
    return {"url": infra["DATABASE_URL"], "result": result}


async def test_counts_and_invalid_marker(loaded: dict[str, Any]) -> None:
    stats = loaded["result"].stats
    assert stats["staging_companies"] == 2424
    async with pg_connection(loaded["url"]) as conn:
        marker = await conn.fetchrow("SELECT * FROM core.companies WHERE id = $1", PSEUDO_ID)
        assert marker["is_valid"] is False and marker["invalid_reason"] == "pseudo_id_collision"
        assert marker["source_rows"] == 3 and marker["okved_code"] is None
        assert (
            await conn.fetchval("SELECT count(*) FROM core.company_context WHERE company_id = $1", PSEUDO_ID)
            == 0
        )
        stub = await conn.fetchval(
            "SELECT count(*) FROM core.companies WHERE revenue_stub AND revenue_rub IS NULL"
        )
        assert stub > 0
        assert await conn.fetchval("SELECT count(*) FROM core.companies WHERE revenue_rub = 1000") == 0


async def test_payments_flags(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        row = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE is_self_transfer) AS self_pairs,
                      count(*) FILTER (WHERE NOT payer_known) AS unknown_payers,
                      count(*) FILTER (WHERE party_invalid) AS invalid_party
               FROM core.payments_agg"""
        )
        assert row["self_pairs"] > 0 and row["unknown_payers"] > 0 and row["invalid_party"] > 0
        dup = await conn.fetchrow(
            "SELECT * FROM core.payments_agg WHERE payer_id = $1 AND beneficiary_id = 'U_700002'", PSEUDO_ID
        )
        assert dup["source_rows"] == 2 and dup["party_invalid"] is True
        # graph edges never involve unknown or invalid companies, nor self-transfers
        bad = await conn.fetchval(
            """SELECT count(*) FROM core.company_edges e
               LEFT JOIN core.companies a ON a.id = e.src_id AND a.is_valid
               LEFT JOIN core.companies b ON b.id = e.dst_id AND b.is_valid
               WHERE a.id IS NULL OR b.id IS NULL OR e.src_id = e.dst_id"""
        )
        assert bad == 0


async def test_graph_and_hypotheses(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        types = dict(
            await conn.fetch("SELECT edge_type || ':' || label, count(*) FROM core.company_edges GROUP BY 1")
        )
        assert {"payment:", "shared_person:", "hypothesis:key_supplier", "hypothesis:key_customer",
                "hypothesis:affiliated_outside_group"} <= set(types)  # fmt: skip
        hyp = await conn.fetch("SELECT * FROM core.company_edges WHERE is_hypothesis")
        assert all(0 < h["confidence"] < 1 for h in hyp)
        assert all("statement" in json.loads(h["evidence"]) for h in hyp)
        link = await conn.fetchrow(
            "SELECT * FROM core.company_edges"
            " WHERE edge_type='payment' AND src_id='U_700005' AND dst_id='U_700001'"
        )
        assert link is not None and 0 < link["share_src"] <= 1


async def test_demo_contexts(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        rows = {
            r["company_id"]: r
            for r in await conn.fetch(
                "SELECT * FROM core.company_context WHERE company_id = ANY($1::text[])", [d.id for d in DEMO]
            )
        }
    assert set(rows) == {d.id for d in DEMO}
    metals = rows["U_700001"]
    feats = json.loads(metals["features"])
    assert metals["rate_sensitive"] and metals["fx_sensitive"]
    assert set(metals["fts_countries"]) == {"КИТАЙ", "ТУРЦИЯ", "КАЗАХСТАН"}
    assert "steel" in metals["commodities"] and "construction" in metals["counterparty_tags"]
    assert feats["counterparties.n_in"]["value"] >= 30
    assert feats["group.size"]["value"] == 6
    assert rows["U_700005"]["segment"] == "large"  # group revenue > 15 bn
    retail = rows["U_700002"]
    assert not retail["fx_sensitive"] and not retail["rate_sensitive"]
    assert json.loads(retail["features"])["sensitivities.consumer_demand_dependent"]["value"] is True


async def test_quality_report(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        checks = {
            r["check_name"]: r
            for r in await conn.fetch(
                "SELECT * FROM core.data_quality_report WHERE run_id = $1", loaded["result"].run_id
            )
        }
    assert checks["duplicate_id_rows"]["value"] == 3
    assert json.loads(checks["duplicate_id_rows"]["details"])["ids"] == {PSEUDO_ID: 3}
    assert checks["fts_flag_0_with_countries"]["value"] == 3
    assert checks["fts_flag_1_without_countries"]["value"] == 3
    assert checks["self_transfer_rows"]["value"] > 0
    assert checks["rejected_rows"]["value"] == 0


async def test_user_profile_survives_reingest_and_changes_context(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        await conn.execute(
            """UPDATE core.company_context SET user_provided = '{"credit_plans": "кредит 300 млн в 2027",
               "currencies": ["CNY"]}'::jsonb WHERE company_id = 'U_700002'"""
        )
        before = await conn.fetchval(
            "SELECT context_version FROM core.company_context WHERE company_id = 'U_700002'"
        )
        await rebuild_contexts(conn, ["U_700002"])
        row = await conn.fetchrow("SELECT * FROM core.company_context WHERE company_id = 'U_700002'")
        assert row["context_version"] == before + 1
        assert row["rate_sensitive"] and row["fx_sensitive"]
        assert json.loads(row["features"])["sensitivities.rate_sensitive"]["source"] == "user"

        result = await ingest_companies(conn, SYNTH, REF)  # full re-run is idempotent
        assert result.stats["companies_valid"] == loaded["result"].stats["companies_valid"]
        assert result.stats["edges"] == loaded["result"].stats["edges"]
        row2 = await conn.fetchrow("SELECT * FROM core.company_context WHERE company_id = 'U_700002'")
        assert json.loads(row2["user_provided"])["currencies"] == ["CNY"]
        assert row2["context_version"] == before + 2 and row2["rate_sensitive"]


async def test_demo_selection_prefers_rich_clients(loaded: dict[str, Any]) -> None:
    async with pg_connection(loaded["url"]) as conn:
        chosen = await select_demo_companies(conn)
    ids = {c["company_id"] for c in chosen}
    assert {"U_700001", "U_700003", "U_700004"} <= ids
    assert len(ids) == len(chosen)


async def test_bad_header_is_rejected(infra: dict[str, str], tmp_path: Path) -> None:
    for name in ("df_Uid_data_hack.csv", "df_Uid_payment_hack.csv", "df_Fid_Uid_link_hack.csv"):
        (tmp_path / name).write_text((SYNTH / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "df_Uid_payment_hack.csv").write_text("PAYER;BENEFICIARY;PAY_AMT;PAY_CNT\n", encoding="utf-8")
    async with pg_connection(infra["DATABASE_URL"]) as conn:
        with pytest.raises(IngestError):
            await ingest_companies(conn, tmp_path, REF)
