"""Deterministic self-check of an assembled insight.

Blocking problems mirror the backend validator (backend/app/domain/insight_validator.py): an insight that fails
here would be rejected there, so the agent fixes or falls back before answering. Quality problems are the
case's main risks (FAQ п.47): retelling instead of interpretation, weak personalisation, unsupported claims,
mixing bank analytics with model conclusions. They trigger a revision round, not a rejection.
"""

from dataclasses import dataclass, field
from typing import Any

from alfa_ml.agent.evidence import Evidence
from alfa_ml.agent.textutil import overlap
from alfa_ml.contracts import schema_errors
from alfa_ml.impact import level_for_score
from alfa_ml.profile import feature_known

SOURCE_KINDS = {"fact", "bank_analytics"}


@dataclass
class CheckResult:
    blocking: list[dict[str, str]] = field(default_factory=list)
    quality: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def add(self, blocking: bool, code: str, detail: str) -> None:
        (self.blocking if blocking else self.quality).append({"code": code, "detail": detail[:300]})

    def as_dict(self) -> dict[str, Any]:
        return {"blocking": self.blocking, "quality": self.quality, "metrics": self.metrics}


def check_insight(insight: dict[str, Any], ev: Evidence) -> CheckResult:
    res = CheckResult()
    for err in schema_errors(insight):
        res.add(True, "schema", err)
    if res.blocking:
        return res
    paths = ev.feature_paths
    source_ids = {s["id"] for s in insight["sources"]}
    statement_ids = {s["id"] for s in insight["statements"]}
    if len(source_ids) != len(insight["sources"]):
        res.add(True, "duplicate_source_id", "sources[].id must be unique")
    if len(statement_ids) != len(insight["statements"]):
        res.add(True, "duplicate_statement_id", "statements[].id must be unique")
    for st in insight["statements"]:
        if st["kind"] in SOURCE_KINDS and not st["source_refs"]:
            res.add(True, "fact_without_source", st["id"])
    refs: list[str] = []
    for st in insight["statements"]:
        refs += st["source_refs"]
    for kf in insight["event"]["key_facts"]:
        refs += kf["source_refs"]
    refs += [p["source_ref"] for p in insight["bank_view"]["positions"]]
    refs += [a["source_ref"] for a in insight["actions"] if a.get("source_ref")]
    for r in sorted(set(refs) - source_ids):
        res.add(True, "unknown_source_ref", r)
    st_refs: list[str] = []
    for block in (
        insight["why_it_matters"],
        insight["consequences"]["risks"],
        insight["consequences"]["opportunities"],
        insight["consequences"].get("scenarios", []),
    ):
        for item in block:
            st_refs += item.get("statement_refs", [])
    for r in sorted(set(st_refs) - statement_ids):
        res.add(True, "unknown_statement_ref", r)
    frefs: list[str] = []
    for item in insight["why_it_matters"]:
        frefs += item["feature_refs"]
    for st in insight["statements"]:
        frefs += st.get("feature_refs", [])
    for q in insight["impact"].get("quantitative", []):
        frefs += q.get("feature_refs", [])
    frefs += insight["relevance"].get("matched_features", [])
    for r in sorted({r for r in frefs if not feature_known(r, paths)}):
        res.add(True, "unknown_feature_ref", r)
    chunk_ids = {a.hit["chunk_id"] for a in ev.analytics}
    for p in insight["bank_view"]["positions"]:
        if p["analytics_chunk_id"] not in chunk_ids:
            res.add(True, "unknown_analytics_chunk", p["analytics_chunk_id"])
    bv = insight["bank_view"]
    if (bv["coverage"] == "none") != (not bv["positions"]):
        res.add(True, "bank_view_inconsistent", f"coverage={bv['coverage']}")
    if bv["model_disagrees_with_bank"] and not (bv.get("disagreement_note") or "").strip():
        res.add(True, "disagreement_without_note", "model_disagrees_with_bank=true")
    if insight["impact"]["level"] != level_for_score(insight["impact"]["score"]):
        res.add(
            True, "impact_level_mismatch", f"{insight['impact']['score']} -> {insight['impact']['level']}"
        )
    for a in insight["actions"]:
        if a.get("product_id") and a["product_id"] not in ev.product_ids:
            res.add(True, "unknown_product", a["product_id"])
    if insight["company_id"] != ev.company_id:
        res.add(True, "company_mismatch", insight["company_id"])

    # --- quality (case risks) --------------------------------------------------------------------------
    news_text = " ".join(f"{n.title} {n.text}" for n in ev.news)
    # the title names the event on purpose ("<event>: <effect for the company>"); retelling is judged on the summary
    retell = overlap(insight["summary"], news_text) if news_text else 0.0
    res.metrics["retelling_overlap"] = round(retell, 3)
    if retell > 0.8:
        res.add(
            False,
            "retelling",
            "summary mostly repeats the news text — explain the effect on the company",
        )
    feats = {f for item in insight["why_it_matters"] for f in item["feature_refs"]}
    res.metrics["personal_features"] = float(len(feats))
    if len(feats) < 1:
        res.add(False, "weak_personalisation", "why_it_matters cites no company features")
    kinds = [s["kind"] for s in insight["statements"]]
    grounded = sum(1 for s in insight["statements"] if s["source_refs"])
    res.metrics["grounded_share"] = round(grounded / max(1, len(kinds)), 3)
    res.metrics["n_statements"] = float(len(kinds))
    if not (insight["consequences"]["risks"] or insight["consequences"]["opportunities"]):
        res.add(False, "no_consequences", "no risks or opportunities")
    has_bank_claim = "bank_analytics" in kinds
    if ev.analytics and not insight["bank_view"]["positions"]:
        res.add(False, "bank_view_unused", "analytics chunks were found but the bank position is empty")
    if insight["bank_view"]["positions"] and not has_bank_claim:
        res.add(False, "bank_claim_missing", "bank position without a bank_analytics statement")
    for st in insight["statements"]:
        if st["kind"] == "model_inference" and any(
            w in st["text"].lower() for w in ("альфа-банк ожидает", "аналитики банка", "по мнению банка")
        ):
            res.add(False, "attribution", f"{st['id']}: model inference phrased as the bank's view")
    return res
