"""Blind check of the shipped ml-ranker@2 artifact (alfa_ml/models/ranker2.json) against ml-ranker@1.

    cd ml && python -m eval.semantic_store dump --sets set5 --out eval/out/semantic_texts_set5.json
    python -m eval.semantic_store compute --texts eval/out/semantic_texts_set5.json   # fastembed env
    python -m eval.eval_ranker2 --sets set5

No training here: the weights and the threshold are exactly those the service loads.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alfa_ml import ranker2, semantic
from eval.datasets import SETS
from eval.train_ranker2 import HERE, RULE_THRESHOLD, add_knn, featurize, metrics


def run(name: str) -> dict[str, dict[str, float]]:
    rows = featurize(name)
    mem = ranker2.load_memory()
    assert mem is not None, "no alfa_ml/models/ranker2_memory.npz"
    add_knn(rows, mem, own_set_out=False)  # exactly what the service computes
    thr = ranker2.threshold()
    p = [ranker2.predict(r["x"])[0] for r in rows]
    return {
        "ml-ranker@1": metrics(
            rows, [r["rule"] for r in rows], [r["has_exposure"] and r["rule"] >= RULE_THRESHOLD for r in rows]
        ),
        "ml-ranker@2": metrics(
            rows, p, [r["has_exposure"] and q >= thr for r, q in zip(rows, p, strict=True)]
        ),
        "_errors": {"rows": rows, "p": p},  # type: ignore[dict-item]
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="set5")
    ap.add_argument("--store", default=str(HERE / "out" / "semantic_store.json"))
    ap.add_argument("--report", default=str(HERE.parent / "reports" / "ranker2_blind.md"))
    a = ap.parse_args()
    semantic.set_backend(semantic.StoreBackend(Path(a.store)))
    m = ranker2.load_model()
    assert m is not None, "no alfa_ml/models/ranker2.json"
    lines = [
        "# Слепая проверка поставляемой модели ml-ranker@2", "",
        f"Сгенерировано `ml/eval/eval_ranker2.py`. Модель — файл `alfa_ml/models/ranker2.json` как есть "
        f"(обучена {m['trained_at']} на {', '.join(m['train_sets'])}; порог {m['threshold']}), без дообучения. "
        "Разметка наборов закоммичена до прогона.", "",
    ]  # fmt: skip
    for name in a.sets.split(","):
        assert name not in m["train_sets"], (
            f"{name} is in the training data (and in the kNN memory): not blind"
        )
        res = run(name)
        miss = getattr(semantic.backend(), "missing", [])
        assert not miss, f"semantic store misses {len(miss)} texts: run eval.semantic_store for {name}"
        err = res.pop("_errors")
        lines += [f"## {SETS[name].note}", "",
                  "| Модель | nDCG@5 | P@3 | Recall важных | Recall релевантных | Доля мусора | Показано |",
                  "|---|---|---|---|---|---|---|"]  # fmt: skip
        for k, v in res.items():
            lines.append(
                f"| {k} | {v['ndcg5']:.3f} | {v['p3']:.3f} | {v['recall_important']:.2f} | "
                f"{v['recall_relevant']:.2f} | {v['noise'] * 100:.0f}% | {int(v['shown'])}/{int(v['pairs'])} |"
            )
        thr = ranker2.threshold()
        fp = [
            (q, r)
            for r, q in zip(err["rows"], err["p"], strict=True)
            if r["has_exposure"] and q >= thr and r["gold"] == 0
        ]
        fn = [
            (q, r)
            for r, q in zip(err["rows"], err["p"], strict=True)
            if r["gold"] >= 1 and not (r["has_exposure"] and q >= thr)
        ]
        lines += ["", "Ошибки ml-ranker@2 (для разбора, не для подгонки):", ""]
        lines += [
            f"- показано лишнее: {r['company_id']} · {r['title'][:90]} (p={q:.2f})"
            for q, r in sorted(fp, key=lambda t: -t[0])
        ]
        lines += [
            f"- пропущено (gold={r['gold']}): {r['company_id']} · {r['title'][:90]} (p={q:.2f}, каналы правил: {'да' if r['has_exposure'] else 'нет'})"
            for q, r in sorted(fn, key=lambda t: -t[1]["gold"])
        ]
        lines.append("")
    Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
