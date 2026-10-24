"""ml-ranker@2: learned relevance of an event to a company (ADR-ML-011).

Inputs per (company, event) pair:
* rule channels of `profile.compute_exposure` (rate, FX/country, industry, counterparties, commodity, demand, …)
  and the ml-ranker@1 score — interpretable, but tied to the backend's keyword tags;
* event screening flags (`profile.screen_event`) and the event type;
* semantic features (`semantic.semantic_features`): similarity of the event to "what moves a business"
  concepts, gated by what concerns this company; similarity to the company profile; cross-encoder score.

Model: standardised logistic regression trained on the labelled sets (eval/train_ranker2.py) with
leave-one-set-out validation; the weights, scaler and threshold live in `models/ranker2.json`. Logistic
regression on purpose: tiny, deterministic, explainable (each feature's contribution goes to the trace), and
with ~2k labelled pairs a boosted model overfits (see reports/ranker2.md).
Fallback: without weights or without the semantic layer the agent keeps ml-ranker@1.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from alfa_ml import semantic
from alfa_ml.impact import magnitude
from alfa_ml.profile import PROXIMITY, ExposureResult

NAME = "ml-ranker@2"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "ranker2.json"
EVENT_TYPES = (
    "monetary_policy", "fx", "sanctions", "regulation_tax", "trade_logistics", "commodities", "industry",
    "macro_stat", "labor", "company", "other",
)  # fmt: skip
SCREEN = ("business", "russia", "cyrillic", "ru_rate", "trade", "market", "rate_in_title", "foreign_geo")


def rule_features(exposure: ExposureResult, event: dict[str, Any], rule_score: float) -> dict[str, float]:
    out = {f"w_{k}": 0.0 for k in PROXIMITY}
    for e in exposure.items.values():
        out[f"w_{e.name}"] = max(out.get(f"w_{e.name}", 0.0), round(e.weight, 4))
    out["rule_score"] = float(rule_score)
    out["n_channels"] = float(len(exposure.items))
    out["magnitude"] = float(magnitude(event))
    scr = event.get("ml_screen") or {}
    for k in SCREEN:
        out[f"s_{k}"] = 1.0 if scr.get(k) else 0.0
    et = str(event.get("event_type") or "other")
    for t in EVENT_TYPES:
        out[f"t_{t}"] = 1.0 if et == t else 0.0
    return out


def pair_features(
    features: dict[str, Any], event: dict[str, Any], exposure: ExposureResult, rule_score: float
) -> dict[str, float]:
    out = rule_features(exposure, event, rule_score)
    out.update(semantic.semantic_features(features, event))
    return out


@lru_cache(maxsize=1)
def load_model() -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def available() -> bool:
    m = load_model()
    return m is not None and (not m.get("requires_semantic", True) or semantic.available())


def predict(x: dict[str, float]) -> tuple[float, dict[str, float]]:
    """(probability, per-feature contribution to the logit) for the explanation in the trace."""
    m = load_model()
    assert m is not None
    z = float(m["intercept"])
    contrib: dict[str, float] = {}
    for name, w, mu, sd in zip(m["features"], m["coef"], m["mean"], m["scale"], strict=True):
        c = float(w) * ((float(x.get(name, 0.0)) - float(mu)) / (float(sd) or 1.0))
        contrib[name] = c
        z += c
    return 1.0 / (1.0 + math.exp(-z)), contrib


def threshold() -> float:
    m = load_model()
    return float(m["threshold"]) if m else 1.0


def explain(contrib: dict[str, float], n: int = 5) -> list[dict[str, float | str]]:
    top = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))[:n]
    return [{"feature": k, "contribution": round(v, 3)} for k, v in top]
