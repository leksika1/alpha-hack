import csv
from collections import Counter
from pathlib import Path

from app.services import synthetic
from app.services.ingest_companies import COMPANY_COLUMNS, LINK_COLUMNS, PAYMENT_COLUMNS

SYNTH_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"


def test_generation_is_deterministic() -> None:
    assert synthetic.generate(7) == synthetic.generate(7)
    assert synthetic.generate(7) != synthetic.generate(8)


def test_headers_match_ingest_expectations() -> None:
    assert synthetic.COMPANY_HEADER == COMPANY_COLUMNS
    assert synthetic.PAYMENT_HEADER == PAYMENT_COLUMNS
    assert synthetic.LINK_HEADER == LINK_COLUMNS


def test_reproduces_real_data_quirks() -> None:
    data = synthetic.generate()
    ids = Counter(r[0] for r in data["companies"])
    assert ids[synthetic.PSEUDO_ID] == 3
    assert any(r[20] == "1000.0" for r in data["companies"])  # revenue stub
    assert any(r[4] == "" for r in data["companies"])  # blank ECO label
    known = set(ids)
    assert any(p[0] == p[1] for p in data["payments"])  # self-transfers
    assert any(p[1] not in known for p in data["payments"])  # unknown payer
    pairs = Counter((p[1], p[0]) for p in data["payments"])
    assert max(pairs.values()) >= 2  # duplicated pair rows
    assert all(float(p[2]) % 1000 == 0 for p in data["payments"])  # amounts rounded to thousands
    assert any(r[2] in {"", "ИНН не определён", "Не определено"} for r in data["links"])
    assert any('"' in r[1] for r in data["links"])


def test_demo_companies_are_rich_clients() -> None:
    data = synthetic.generate()
    rows = {r[0]: r for r in data["companies"]}
    for d in synthetic.DEMO:
        row = rows[d.id]
        assert row[10] == "CLIENT"
        n_cp = sum(1 for p in data["payments"] if d.id in (p[0], p[1]) and p[0] != p[1])
        assert n_cp >= 30, d.id
    assert len({d.okved[:2] for d in synthetic.DEMO}) == len(synthetic.DEMO)  # different industries


def test_committed_files_are_up_to_date() -> None:
    """data/synthetic must be regenerated (make synthetic) after generator changes."""
    data = synthetic.generate()
    files = {
        "companies": "df_Uid_data_hack.csv",
        "payments": "df_Uid_payment_hack.csv",
        "links": "df_Fid_Uid_link_hack.csv",
    }
    for key, name in files.items():
        with (SYNTH_DIR / name).open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        assert rows[1:] == data[key], name
