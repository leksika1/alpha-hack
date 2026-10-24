"""Precompute embeddings and reranker scores for the labelled sets (offline training/eval of ml-ranker@2).

Two steps, because the eval environment (pandas, sklearn) and the model runtime (fastembed) may be different
Python environments:
    python -m eval.semantic_store dump    --out eval/out/semantic_texts.json     # needs the CSVs
    python -m eval.semantic_store compute --texts eval/out/semantic_texts.json --out eval/out/semantic_store.json
In the service the same texts are embedded on the fly (alfa_ml/semantic.py, LRU cache).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def dump(out: Path, names: list[str]) -> None:
    from alfa_ml import semantic
    from alfa_ml.profile import normalize_event
    from eval.datasets import SETS, load_pairs

    texts: dict[str, None] = dict.fromkeys(semantic.CONCEPTS.values())
    pairs: dict[tuple[str, str], None] = {}
    for name in names:
        s = SETS[name]
        if (s.news and not s.news.exists()) or not s.labels.exists():
            print(f"skip {name}: files missing")
            continue
        rows = load_pairs(name)
        for r in rows:
            ev = normalize_event(r["event"])
            et = semantic.event_text(ev)
            texts[et] = None
            texts[semantic.profile_text(r["features"])] = None
            pairs[(semantic.rerank_query(r["features"]), et)] = None
        print(f"{name}: {len(rows)} pairs")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"texts": list(texts), "pairs": [list(p) for p in pairs]}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(texts)} texts, {len(pairs)} rerank pairs -> {out}")


def compute(texts_path: Path, out: Path) -> None:
    from alfa_ml import semantic

    semantic.RERANK_MODEL = (
        semantic.RERANK_MODEL or semantic.EVAL_RERANK_MODEL
    )  # the store keeps the ablation
    data = json.loads(texts_path.read_text(encoding="utf-8"))
    store = {"vectors": {}, "rerank": {}}
    if out.exists():
        store = json.loads(out.read_text(encoding="utf-8"))
    b = semantic.FastembedBackend()
    todo = [t for t in data["texts"] if semantic._key(semantic.EMBED_MODEL, t) not in store["vectors"]]
    t0 = time.time()
    for i in range(0, len(todo), 64):
        chunk = todo[i : i + 64]
        for t, v in zip(chunk, b.embed(chunk), strict=True):
            store["vectors"][semantic._key(semantic.EMBED_MODEL, t)] = [round(x, 6) for x in v]
    print(f"embedded {len(todo)} texts in {time.time() - t0:.0f}s")
    by_query: dict[str, list[str]] = {}
    for q, d in data["pairs"]:
        if semantic._key(semantic.RERANK_MODEL, q, d) not in store["rerank"]:
            by_query.setdefault(q, []).append(d)
    t0 = time.time()
    n = 0
    for q, docs in by_query.items():
        for d, s in zip(docs, b.rerank(q, docs), strict=True):
            store["rerank"][semantic._key(semantic.RERANK_MODEL, q, d)] = round(float(s), 5)
            n += 1
    print(f"reranked {n} pairs in {time.time() - t0:.0f}s")
    out.write_text(json.dumps(store), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--out", default="eval/out/semantic_texts.json")
    d.add_argument("--sets", default="demo,heldout1,live,set2,set3")
    c = sub.add_parser("compute")
    c.add_argument("--texts", default="eval/out/semantic_texts.json")
    c.add_argument("--out", default="eval/out/semantic_store.json")
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(Path(a.out), a.sets.split(","))
    else:
        compute(Path(a.texts), Path(a.out))


if __name__ == "__main__":
    main()
