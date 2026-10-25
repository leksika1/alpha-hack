"""Train and validate ml-ranker@2 (ADR-ML-011).

    cd ml && python -m eval.semantic_store dump && python -m eval.semantic_store compute   # fastembed env
    cd ml && python -m eval.train_ranker2        # + scikit-learn; needs data/raw (ML_RAW_DATA) and live news

Protocol (no peeking):
* training sets: demo, heldout1, live, set2 (all labels made before the models they were first used on);
* leave-one-set-out: train on three sets, pick the threshold on out-of-fold predictions of the training sets
  only, report on the held-out set; models compared on the same folds: ml-ranker@1 (rules), LR on rule
  features, LR on rules + embeddings (the chosen ml-ranker@2), + reranker, + per-concept features, boosted
  trees; the feature set and C were chosen on the leave-one-set-out mean only;
* the final model is trained on all training sets; BLIND_SETS (labels committed before the model version) are
  scored once at the end and reported as the blind result. A blind set that was looked at becomes a training set
  of the next version (set3, set4 were blind for the 24.10 version).
Writes alfa_ml/models/ranker2.json and reports/ranker2.md.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold

from alfa_ml import ranker2, semantic
from alfa_ml.profile import compute_exposure, normalize_event
from alfa_ml.ranker import score_exposure
from eval.datasets import SETS, load_pairs

HERE = Path(__file__).resolve().parent
TRAIN_SETS = ["demo", "heldout1", "live", "set2", "set3", "set4", "set5"]
BLIND_SETS = ["set6"]  # the next blind set; labelled and committed before a model version, scored once
RULE_THRESHOLD = 0.35
MIN_RECALL_IMPORTANT = 0.95
# Chosen on the leave-one-set-out mean only (never on a blind set): five aggregate embedding features, the kNN label
# memory, C = 0.1, threshold by F1 under recall(important) >= 0.95. The per-concept features (c_*, cn_*) and the
# reranker made the model less stable across sets; the precision-only threshold lost important news on set4.
SEM_AGG = ("sem_profile", "sem_best_pos", "sem_best_neg", "sem_margin", "sem_need_match")
KNN = ("knn_rel", "knn_imp", "knn_mass")
LR_C = 0.1


def featurize(name: str) -> list[dict[str, Any]]:
    rows = []
    now = datetime.now(UTC)
    for p in load_pairs(name, now):
        ev = normalize_event(p["event"])
        exp = compute_exposure(p["features"], ev, p["company_id"])
        pre = score_exposure(exp, ev, "cfo", now=now)
        x = ranker2.pair_features(p["features"], ev, exp, pre["score"], knn=False)
        evv, cov = ranker2.pair_vectors(p["features"], ev)
        rows.append({**{k: p[k] for k in ("set", "company_id", "event_key", "gold")}, "x": x, "ev": evv, "co": cov,
                     "has_exposure": bool(exp.items), "rule": pre["score"], "title": ev.get("title", "")})  # fmt: skip
    return rows


def build_memory(rows: list[dict[str, Any]]) -> ranker2.Memory:
    ev_key: dict[tuple[str, str], int] = {}
    co_key: dict[tuple[str, str], int] = {}
    evs, cos, ei, ci = [], [], [], []
    for r in rows:
        ke, kc = (r["set"], r["event_key"]), (r["set"], r["company_id"])
        if ke not in ev_key:
            ev_key[ke] = len(evs)
            evs.append(r["ev"])
        if kc not in co_key:
            co_key[kc] = len(cos)
            cos.append(r["co"])
        ei.append(ev_key[ke])
        ci.append(co_key[kc])
    return ranker2.Memory(
        np.array(evs, dtype=np.float32), np.array(cos, dtype=np.float32), np.array(ei, dtype=np.int32),
        np.array(ci, dtype=np.int32), np.array([r["gold"] for r in rows], dtype=np.int8),
        np.array([r["set"] for r in rows]),
    )  # fmt: skip


def add_knn(rows: list[dict[str, Any]], mem: ranker2.Memory, own_set_out: bool) -> None:
    """kNN features; for training rows the row's own set is excluded (no label of the same feed leaks in)."""
    for r in rows:
        r["x"].update(ranker2.knn_features(r["ev"], r["co"], mem, r["set"] if own_set_out else None))


def matrix(rows: list[dict[str, Any]], names: list[str]) -> np.ndarray:
    return np.array([[float(r["x"].get(n, 0.0)) for n in names] for r in rows])


def weights(rows: list[dict[str, Any]]) -> np.ndarray:
    size = defaultdict(int)
    for r in rows:
        size[r["set"]] += 1
    mean = statistics.mean(size.values())
    return np.array([(mean / size[r["set"]]) * (3.0 if r["gold"] == 2 else 1.0) for r in rows])


class Model:
    def __init__(self, kind: str, names: list[str]) -> None:
        self.kind, self.names = kind, names

    def fit(self, rows: list[dict[str, Any]]) -> Model:
        x, y, w = matrix(rows, self.names), np.array([r["gold"] >= 1 for r in rows]), weights(rows)
        self.mu, self.sd = x.mean(0), x.std(0) + 1e-6
        z = np.clip((x - self.mu) / self.sd, -ranker2.Z_CLIP, ranker2.Z_CLIP)
        if self.kind == "lr":
            self.m: Any = LogisticRegression(C=LR_C, class_weight="balanced", max_iter=2000).fit(
                z, y, sample_weight=w
            )
        else:
            self.m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=150,
                                                    l2_regularization=1.0, class_weight="balanced").fit(z, y, sample_weight=w)  # fmt: skip
        return self

    def proba(self, rows: list[dict[str, Any]]) -> np.ndarray:
        z = np.clip((matrix(rows, self.names) - self.mu) / self.sd, -ranker2.Z_CLIP, ranker2.Z_CLIP)
        return self.m.predict_proba(z)[:, 1]


def choose_threshold(rows: list[dict[str, Any]], p: np.ndarray) -> float:
    """Threshold with the best F1 (relevant pairs) among those keeping recall(important) >= MIN_RECALL_IMPORTANT."""
    best, best_prec = 0.05, -1.0
    n_rel = max(1, sum(1 for r in rows if r["gold"] >= 1))
    for thr in np.arange(0.05, 0.95, 0.01):
        shown = [(r, q) for r, q in zip(rows, p, strict=True) if r["has_exposure"] and q >= thr]
        imp = [r for r in rows if r["gold"] == 2]
        rec = sum(1 for r, _ in shown if r["gold"] == 2) / max(1, len(imp))
        if rec < MIN_RECALL_IMPORTANT:
            continue
        tp = sum(1 for r, _ in shown if r["gold"] >= 1)
        prec, rec1 = tp / max(1, len(shown)), tp / n_rel
        f1 = 2 * prec * rec1 / (prec + rec1 + 1e-9)
        if f1 >= best_prec:
            best, best_prec = float(thr), f1
    return best


def oof_threshold(kind: str, names: list[str], rows: list[dict[str, Any]]) -> float:
    groups = [f"{r['set']}:{r['event_key']}" for r in rows]
    p = np.zeros(len(rows))
    for tr, te in GroupKFold(n_splits=5).split(rows, groups=groups):
        m = Model(kind, names).fit([rows[i] for i in tr])
        p[te] = m.proba([rows[i] for i in te])
    return choose_threshold(rows, p)


def dcg(rels: list[int]) -> float:
    return sum((2**r - 1) / math.log2(i + 2) for i, r in enumerate(rels))


def metrics(rows: list[dict[str, Any]], score: list[float], shown: list[bool]) -> dict[str, float]:
    by = defaultdict(list)
    for r, s, sh in zip(rows, score, shown, strict=True):
        by[r["company_id"]].append((s if sh else 0.0, r["gold"], sh))
    nd, p3, noise, rec2, rec1 = [], [], [], [], []
    for items in by.values():
        ranked = sorted(items, key=lambda t: -t[0])
        ideal = sorted((g for _, g, _ in items), reverse=True)
        nd.append(dcg([g for _, g, _ in ranked[:5]]) / (dcg(ideal[:5]) or 1.0))
        p3.append(sum(1 for _, g, _ in ranked[:3] if g >= 1) / 3)
        sh = [g for _, g, s in items if s]
        noise.append(sum(1 for g in sh if g == 0) / max(1, len(sh)))
        imp = [s for _, g, s in items if g == 2]
        if imp:
            rec2.append(sum(imp) / len(imp))
        rel = [s for _, g, s in items if g >= 1]
        if rel:
            rec1.append(sum(rel) / len(rel))
    return {
        "ndcg5": statistics.mean(nd), "p3": statistics.mean(p3), "noise": statistics.mean(noise),
        "recall_important": statistics.mean(rec2) if rec2 else float("nan"),
        "recall_relevant": statistics.mean(rec1) if rec1 else float("nan"),
        "shown": float(sum(shown)), "pairs": float(len(rows)),
    }  # fmt: skip


def evaluate(
    kind: str, names: list[str], train: list[dict[str, Any]], test: list[dict[str, Any]]
) -> dict[str, float]:
    if kind == "rules":
        score = [r["rule"] for r in test]
        return metrics(test, score, [r["has_exposure"] and r["rule"] >= RULE_THRESHOLD for r in test])
    thr = oof_threshold(kind, names, train)
    p = Model(kind, names).fit(train).proba(test)
    return {
        **metrics(test, list(p), [r["has_exposure"] and q >= thr for r, q in zip(test, p, strict=True)]),
        "thr": thr,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(HERE / "out" / "semantic_store.json"))
    ap.add_argument("--report", default=str(HERE.parent / "reports" / "ranker2.md"))
    a = ap.parse_args()
    semantic.RERANK_MODEL = semantic.RERANK_MODEL or semantic.EVAL_RERANK_MODEL  # for the ablation row only
    semantic.set_backend(semantic.StoreBackend(Path(a.store)))
    blind_sets = [n for n in BLIND_SETS if n in SETS and SETS[n].labels.exists()]
    data = {n: featurize(n) for n in [*TRAIN_SETS, *blind_sets]}
    missing = getattr(semantic.backend(), "missing", [])
    print({n: len(v) for n, v in data.items()}, "store misses:", len(missing))
    add_knn(
        [r for rows in data.values() for r in rows], build_memory(data["demo"]), False
    )  # registers the names
    all_names = sorted({k for rows in data.values() for r in rows for k in r["x"]})
    rule_names = [n for n in all_names if not (n.startswith(("sem_", "c_", "cn_", "knn_")))]
    emb_names = rule_names + list(SEM_AGG)
    final_names = emb_names + list(KNN)
    variants = {
        "ml-ranker@1 (правила, порог 0,35)": ("rules", []),
        "LR: признаки правил": ("lr", rule_names),
        "LR: правила + эмбеддинги (ml-ranker@2 от 24.10)": ("lr", emb_names),
        "LR: правила + эмбеддинги + kNN-память разметки (ml-ranker@2)": ("lr", final_names),
        "LR: + реранкер": ("lr", [*final_names, "sem_rerank"]),
        "Бустинг: правила + эмбеддинги + kNN": ("gb", final_names),
    }
    lodo: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for test_name in TRAIN_SETS:
        train = [r for n in TRAIN_SETS if n != test_name for r in data[n]]
        mem = build_memory(train)
        add_knn(train, mem, own_set_out=True)
        add_knn(data[test_name], mem, own_set_out=False)
        for vname, (kind, names) in variants.items():
            lodo[vname][test_name] = evaluate(kind, names, train, data[test_name])
            print(test_name, vname, {k: round(v, 3) for k, v in lodo[vname][test_name].items()})

    full = [r for n in TRAIN_SETS for r in data[n]]
    memory = build_memory(full)
    add_knn(full, memory, own_set_out=True)
    thr = oof_threshold("lr", final_names, full)
    final = Model("lr", final_names).fit(full)
    blind: dict[str, dict[str, dict[str, float]]] = {}
    for bn in blind_sets:
        add_knn(data[bn], memory, own_set_out=False)
        blind[bn] = {
            vname: evaluate(kind, names, full, data[bn]) for vname, (kind, names) in variants.items()
        }
        print(bn, {k: {m: round(v, 3) for m, v in d.items()} for k, d in blind[bn].items()})

    model = {
        "name": ranker2.NAME, "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embed_model": semantic.EMBED_MODEL, "requires_semantic": True, "requires_memory": True, "C": LR_C,
        "train_sets": TRAIN_SETS, "pairs": len(full), "positives": int(sum(r["gold"] >= 1 for r in full)),
        "features": final_names, "coef": [round(float(c), 6) for c in final.m.coef_[0]],
        "intercept": round(float(final.m.intercept_[0]), 6), "mean": [round(float(v), 6) for v in final.mu],
        "scale": [round(float(v), 6) for v in final.sd], "threshold": round(thr, 3),
    }  # fmt: skip
    out = HERE.parent / "alfa_ml" / "models" / "ranker2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    memory.save(ranker2.MEMORY_PATH)

    top = sorted(zip(final_names, model["coef"], strict=True), key=lambda kv: -abs(kv[1]))[:15]
    lines = [
        "# ml-ranker@2: эмбеддинги + kNN-память разметки + обученная модель (ADR-ML-011)", "",
        f"Сгенерировано `ml/eval/train_ranker2.py` · {model['trained_at']} · эмбеддинги `{semantic.EMBED_MODEL}`, "
        f"реранкер (абляция) `{semantic.RERANK_MODEL}` · обучение: {', '.join(TRAIN_SETS)} ({len(full)} пар, "
        f"{model['positives']} релевантных) · порог показа {model['threshold']} (out-of-fold, лучший F1 при recall важных ≥ {MIN_RECALL_IMPORTANT}).", "",
        "Протокол: состав признаков, регуляризация и правило порога выбраны только по среднему перекрёстной проверки ниже; "
        "kNN-признаки обучающих строк считаются без их собственного набора; слепые наборы размечены и закоммичены "
        "до обучения и в выборе не участвовали.", "",
        f"## Перекрёстная проверка «один набор — в тест» (обучение на {len(TRAIN_SETS) - 1} остальных)", "",
        "| Модель | Тестовый набор | nDCG@5 | P@3 | Recall важных | Recall релевантных | Доля мусора | Показано |",
        "|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for vname, per in lodo.items():
        for t, m in per.items():
            lines.append(
                f"| {vname} | {t} | {m['ndcg5']:.3f} | {m['p3']:.3f} | {m['recall_important']:.2f} | {m['recall_relevant']:.2f} | {m['noise'] * 100:.0f}% | {int(m['shown'])}/{int(m['pairs'])} |"
            )
    lines += [
        "",
        f"Средние по {len(TRAIN_SETS)} тестам:",
        "",
        "| Модель | nDCG@5 | P@3 | Recall важных | Recall релевантных | Доля мусора |",
        "|---|---|---|---|---|---|",
    ]
    for vname, per in lodo.items():
        avg = {
            k: statistics.mean(m[k] for m in per.values())
            for k in ("ndcg5", "p3", "recall_important", "recall_relevant", "noise")
        }
        lines.append(
            f"| {vname} | {avg['ndcg5']:.3f} | {avg['p3']:.3f} | {avg['recall_important']:.2f} | {avg['recall_relevant']:.2f} | {avg['noise'] * 100:.0f}% |"
        )
    for bn, per_model in blind.items():
        lines += ["", f"## Слепой набор: {SETS[bn].note} (разметка до обучения)", "",
                  "| Модель | nDCG@5 | P@3 | Recall важных | Recall релевантных | Доля мусора | Показано |", "|---|---|---|---|---|---|---|"]  # fmt: skip
        for vname, m in per_model.items():
            lines.append(
                f"| {vname} | {m['ndcg5']:.3f} | {m['p3']:.3f} | {m['recall_important']:.2f} | {m['recall_relevant']:.2f} | {m['noise'] * 100:.0f}% | {int(m['shown'])}/{int(m['pairs'])} |"
            )
    lines += ["", "## Самые весомые признаки финальной модели (стандартизованные коэффициенты)", "",
              "| Признак | Вес |", "|---|---|", *[f"| `{n}` | {c:+.3f} |" for n, c in top]]  # fmt: skip
    Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
