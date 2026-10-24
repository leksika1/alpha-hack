"""ml-ranker@2 with a deterministic fake semantic backend (no model weights in unit tests)."""

import hashlib
import math
from typing import Any

import pytest

from alfa_ml import ranker2, semantic
from alfa_ml.agent.runner import Agent
from tests.fakes import RATE_EVENT, SPORT_EVENT, context_for, generate_request


class HashBackend(semantic.Backend):
    """Bag-of-stems vectors: texts sharing words are similar — enough to test the wiring."""

    name = "hash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in t.lower().split():
                v[int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        q = {w[:5] for w in query.lower().split()}
        return [len(q & {w[:5] for w in d.lower().split()}) / 10 for d in docs]


@pytest.fixture
def fake_semantic(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    semantic.set_backend(HashBackend())
    model = {
        "name": "ml-ranker@2", "requires_semantic": True, "features": ["rule_score", "sem_need_match"],
        "coef": [3.0, 1.0], "intercept": -1.0, "mean": [0.0, 0.0], "scale": [1.0, 1.0], "threshold": 0.5,
    }  # fmt: skip
    monkeypatch.setattr(ranker2, "load_model", lambda: model)
    yield
    semantic.set_backend(None)


def test_profile_text_uses_only_context_features() -> None:
    feats = context_for("U_110108")["features"]
    text = semantic.profile_text(feats)
    assert "металл" in text.lower() and "Кредит".lower() in text.lower() and "Китай" in text
    needs = semantic.company_needs(feats)
    assert needs["rate"] == 1.0 and needs["metals"] == 1.0 and needs["agro"] == 0.0


def test_semantic_features_are_bounded(fake_semantic) -> None:  # type: ignore[no-untyped-def]
    feats = context_for("U_110108")["features"]
    x = semantic.semantic_features(feats, RATE_EVENT)
    assert {"sem_profile", "sem_need_match", "sem_margin", "c_rate", "cn_rate", "c_neg_incident"} <= set(x)
    assert all(-1.0001 <= v <= 1.0001 for k, v in x.items() if k.startswith(("c_", "cn_", "sem_p")))
    assert not math.isnan(sum(x.values()))
    assert "sem_rerank" not in x  # the cross-encoder is off by default (ADR-ML-011 ablation)


async def test_agent_uses_the_learned_ranker(backend, fake_semantic) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(RATE_EVENT["event_id"], allow_llm=False)).run()
    assert res.status == "completed", res.error
    assert res.insight["relevance"]["scorer"] == "ml-ranker@2"  # type: ignore[index]
    plan = next(s for s in backend if s["name"] == "rule_plan")
    assert plan["data"]["learned"]["score"] >= plan["data"]["learned"]["threshold"]
    skipped = await Agent(generate_request(SPORT_EVENT["event_id"], allow_llm=False)).run()
    assert skipped.status == "skipped"


async def test_without_semantic_layer_falls_back_to_rules(backend) -> None:  # type: ignore[no-untyped-def]
    res = await Agent(generate_request(RATE_EVENT["event_id"], allow_llm=False)).run()
    assert res.insight["relevance"]["scorer"] == "ml-ranker@1"  # type: ignore[index]


def test_store_backend_reports_misses(tmp_path: Any) -> None:
    p = tmp_path / "s.json"
    p.write_text('{"vectors": {}, "rerank": {}}')
    b = semantic.StoreBackend(p)
    assert b.embed(["x"]) == [[]] and b.rerank("q", ["d"]) == [0.0] and len(b.missing) == 2
