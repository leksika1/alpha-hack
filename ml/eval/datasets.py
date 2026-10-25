"""Labelled evaluation sets of the relevance model as (company, event, gold) pairs (shared by training and eval).

Each set: news file (backend tagger re-applied, see run_eval.load_events), labels file, CSV source of the company
contexts. The real CSVs (data/raw) and the live news texts (eval/out/live) are not committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.contexts import CsvCompanies
from eval.run_eval import HERE, ROOT, SYN, load_events

RAW = Path(__import__("os").environ.get("ML_RAW_DATA", str(ROOT / "data" / "raw")))


@dataclass
class EvalSet:
    name: str
    news: Path | None
    labels: Path
    data_dir: Path
    note: str


SETS: dict[str, EvalSet] = {
    "demo": EvalSet("demo", None, HERE / "labels.json", SYN, "демо-лента бэкенда (синтетика), 5 компаний × 15 событий"),
    "heldout1": EvalSet(
        "heldout1", HERE / "heldout" / "news_2026-10-23.json", HERE / "heldout" / "labels_2026-10-23.json", RAW,
        "Интерфакс 23–24.10, 10 реальных клиентов × 15 событий",
    ),
    "live": EvalSet(
        "live", HERE / "out" / "live" / "news_live_2026-10-24.json", HERE / "live" / "labels_live_2026-10-24.json", SYN,
        "живая лента dev-стенда, 5 демо-компаний × 331 событие",
    ),
    "set2": EvalSet(
        "set2", HERE / "heldout" / "news_2026-10-24_set2.json", HERE / "heldout" / "labels_2026-10-24_set2.json", RAW,
        "слепой набор №2: Интерфакс, 10 реальных клиентов × 30 событий",
    ),
    "set3": EvalSet(
        "set3", HERE / "heldout" / "news_set3.json", HERE / "heldout" / "labels_set3.json", RAW,
        "слепой набор №3 (финальная проверка ml-ranker@2)",
    ),
    "set4": EvalSet(
        "set4", HERE / "heldout" / "news_set4.json", HERE / "heldout" / "labels_set4.json", RAW,
        "слепой набор №4: «Коммерсантъ» (новый источник), 10 новых реальных клиентов × 40 событий",
    ),
}  # fmt: skip

_CC: dict[Path, CsvCompanies] = {}


def companies(data_dir: Path) -> CsvCompanies:
    if data_dir not in _CC:
        _CC[data_dir] = CsvCompanies(data_dir)
    return _CC[data_dir]


def load_pairs(name: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """[{set, company_id, event_key, event, features, gold}] for one labelled set."""
    s = SETS[name]
    now = now or datetime.now(UTC)
    events = load_events(now, s.news)
    labels = json.loads(s.labels.read_text(encoding="utf-8"))["labels"]
    cc = companies(s.data_dir)
    cids = [c for c in json.loads(s.labels.read_text(encoding="utf-8"))["companies"]]
    out = []
    for cid in cids:
        feats = cc.context(cid)["features"]
        for key, ev in events.items():
            row = labels.get(key) or {}
            gold = row.get(cid)
            if not isinstance(gold, int):
                continue
            out.append(
                {
                    "set": name,
                    "company_id": cid,
                    "event_key": key,
                    "event": ev,
                    "features": feats,
                    "gold": gold,
                }
            )
    return out
