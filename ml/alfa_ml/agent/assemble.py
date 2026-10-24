"""Draft + evidence -> insight by contracts/insight.schema.json.

The assembler is deterministic and defensive: it maps local claim IDs to statement IDs, keeps only references to
sources and features that really exist, downgrades attributions it cannot prove (a "bank_analytics" claim without
an analytics chunk becomes a model inference), verifies bank quotes against chunk text, and computes impact and
relevance itself. The LLM writes text; the assembler owns the facts of the contract.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from alfa_ml.agent.draft import Draft
from alfa_ml.agent.evidence import Evidence, is_demo_material
from alfa_ml.agent.textutil import clip, overlap
from alfa_ml.contracts import DISCLAIMER_MODEL, DISCLAIMER_NOT_ADVICE, SCALE_VERSION, SCHEMA_VERSION
from alfa_ml.impact import base_score, level_for_score, reconcile
from alfa_ml.profile import feature_known
from alfa_ml.ranker import relevance_for

SOURCE_KINDS = {"fact", "bank_analytics"}
INSIGHT_TTL_DAYS = 14
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def assemble(
    draft: Draft,
    ev: Evidence,
    *,
    run_id: str,
    models_used: list[dict[str, str]],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    sources_all = ev.all_sources()
    known_sources = set(sources_all)
    analytics_sids = {a.sid for a in ev.analytics}
    news_sids = [n.sid for n in ev.news]
    paths = ev.feature_paths
    used_sources: set[str] = set()
    notes: list[str] = []  # assembler corrections, reported in the trace

    def feats(refs: list[str]) -> list[str]:
        out = []
        for r in refs:
            r = r.strip()
            if (
                r
                and feature_known(r, paths)
                and r not in out
                and re.match(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*$", r)
            ):
                out.append(r)
            elif r:
                notes.append(f"unknown feature dropped: {r}")
        return out

    def srcs(refs: list[str]) -> list[str]:
        out = []
        for r in refs:
            if r in known_sources and r not in out:
                out.append(r)
            elif r:
                notes.append(f"unknown source dropped: {r}")
        used_sources.update(out)
        return out

    # --- statements ---------------------------------------------------------------------------------------
    statements: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    for c in draft.claims:
        text = clip(c.text, 600)
        if len(text) < 3:
            continue
        kind = c.kind
        source_refs = srcs(c.sources)
        if kind == "bank_analytics":
            bank_refs = [s for s in source_refs if s in analytics_sids]
            if not bank_refs:
                kind = "model_inference"
                notes.append(f"{c.id}: bank_analytics without analytics source -> model_inference")
            elif (
                any(is_demo_material(ev.analytics_by_sid(s) or {}) for s in bank_refs)
                and "демо" not in text.lower()
            ):
                text = clip("Демо-материал (не аналитика Альфа-Банка): " + text, 600)
        if kind == "fact" and not source_refs:
            kind = "model_inference"
            notes.append(f"{c.id}: fact without source -> model_inference")
        conf = c.confidence
        if kind == "hypothesis":
            conf = min(conf, 0.6)
        sid = f"st{len(statements) + 1}"
        id_map[c.id] = sid
        statements.append(
            {
                "id": sid,
                "text": text,
                "kind": kind,
                "confidence": round(conf, 2),
                "source_refs": source_refs,
                "feature_refs": feats(c.features),
            }
        )

    def st_refs(refs: list[str]) -> list[str]:
        return [id_map[r] for r in refs if r in id_map]

    # --- event --------------------------------------------------------------------------------------------
    e = ev.event
    key_facts = []
    for f in draft.key_facts:
        refs = [s for s in srcs(f.sources) if s in set(news_sids) | {"sc"}]
        if refs and len(f.text.strip()) >= 3:
            key_facts.append({"text": clip(f.text, 500), "source_refs": refs})
    if not key_facts and news_sids:
        key_facts.append(
            {"text": clip(str(e.get("title") or "Событие"), 500), "source_refs": srcs(news_sids[:3])}
        )
    event_summary = draft.event_summary or str(e.get("summary") or "") or str(e.get("title") or "")
    event_obj = {
        "title": clip(str(e.get("title") or "Событие"), 300),
        "summary": clip(
            event_summary if len(event_summary) >= 10 else f"{e.get('title')}. Подробности в источнике.", 1500
        ),
        "event_type": draft.event_type or str(e.get("event_type") or "other"),
        "occurred_at": e.get("first_seen_at"),
        "participants": [
            {"name": clip(p.name, 120), "type": p.type, "company_id": None}
            for p in draft.participants
            if p.name.strip()
        ][:6],
        "key_facts": key_facts[:5],
    }

    # --- why it matters -----------------------------------------------------------------------------------
    why = []
    for r in draft.why_it_matters:
        fr = feats(r.features)
        if not fr or len(r.text.strip()) < 10:
            continue
        why.append({"text": clip(r.text, 700), "feature_refs": fr, "statement_refs": st_refs(r.claims)})
    if not why and ev.exposure.items:
        for x in ev.exposure.ordered()[:3]:
            why.append({"text": x.reason, "feature_refs": x.features, "statement_refs": []})
        notes.append("why_it_matters rebuilt from exposure")

    def cons(items: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "text": clip(c.text, 600),
                "horizon": c.horizon,
                "drivers": [clip(d, 160) for d in c.drivers if len(d) >= 3][:5],
                "statement_refs": st_refs(c.claims),
            }
            for c in items
            if len(c.text.strip()) >= 10
        ]

    scenarios = [
        {
            "name": clip(s.name, 120),
            "logic": clip(s.logic, 600),
            "plausibility": s.plausibility,
            "signals": [clip(x, 200) for x in s.signals][:5],
            "statement_refs": st_refs(s.claims),
        }
        for s in draft.scenarios
        if len(s.name.strip()) >= 3 and len(s.logic.strip()) >= 10
    ]

    # --- bank view ----------------------------------------------------------------------------------------
    positions = []
    for p in draft.bank_positions:
        hit = ev.analytics_by_sid(p.source)
        if not hit or len(p.text.strip()) < 10:
            if p.source:
                notes.append(f"bank position without analytics chunk dropped: {p.source}")
            continue
        quote = p.quote
        if quote and _norm(quote) not in _norm(str(hit.get("text", ""))):
            notes.append(f"{p.source}: quote not found in chunk -> removed")
            quote = None
        text = p.text
        if is_demo_material(hit) and "демо" not in text.lower():
            text = "Демо-материал (синтетика), не позиция Альфа-Банка: " + text
        used_sources.add(p.source)
        positions.append(
            {
                "text": clip(text, 800),
                "quote": clip(quote, 590) if quote else None,
                "analytics_chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "page": int(hit.get("page") or 1),
                "source_ref": p.source,
            }
        )
        if len(positions) >= 3:
            break
    if positions:
        title_text = f"{e.get('title', '')} {e.get('summary', '')}"
        # "direct" only if what we show from the material is about the event itself, not just the chunk
        direct = any(
            overlap(str(e.get("title", "")), str(p["quote"] or p["text"])) >= 0.35 for p in positions
        )
        coverage = "direct" if direct and title_text else "related"
    else:
        coverage = "none"
    disagrees = bool(
        draft.model_disagrees_with_bank and positions and (draft.disagreement_note or "").strip()
    )
    bank_view = {
        "coverage": coverage,
        "positions": positions,
        "model_disagrees_with_bank": disagrees,
        "disagreement_note": clip(draft.disagreement_note or "", 600) if disagrees else None,
    }

    # --- impact & relevance -------------------------------------------------------------------------------
    score, rationale = base_score(ev.exposure, e)
    score, shifted = reconcile(score, draft.impact_level)
    if draft.impact_rationale and len(draft.impact_rationale) >= 10:
        rationale = f"{clip(draft.impact_rationale, 400)} ({rationale})"
    if shifted:
        rationale += " Оценка скорректирована аналитиком в пределах 15 пунктов."
    quantitative = []
    for q in draft.quantitative:
        if len(q.basis.strip()) < 10 or len(q.metric.strip()) < 2 or not q.unit.strip():
            continue
        quantitative.append(
            {
                "metric": clip(q.metric, 80),
                "value": float(q.value),
                "unit": clip(q.unit, 80),
                "basis": clip(q.basis, 600),
                "period": q.period,
                "feature_refs": feats(q.features),
            }
        )
    impact = {
        "level": level_for_score(score),
        "score": int(score),
        "scale_version": SCALE_VERSION,
        "rationale": clip(rationale, 900),
        "quantitative": quantitative[:4],
    }
    relevance = relevance_for(ev, impact_score=score)
    relevance["matched_features"] = feats(relevance.get("matched_features", []))

    # --- actions ------------------------------------------------------------------------------------------
    actions = []
    for a in draft.actions:
        if len(a.text.strip()) < 5:
            continue
        pid = a.product_id if a.product_id in ev.product_ids else None
        src = a.source if a.source in known_sources else None
        if a.type == "read_source" and not src:
            continue
        if src:
            used_sources.add(src)
        actions.append({"type": a.type, "text": clip(a.text, 300), "product_id": pid, "source_ref": src})
        if len(actions) >= 6:
            break

    # --- sources (only referenced ones, event news always) ------------------------------------------------
    used_sources.update(news_sids[:5])
    for st in statements:
        used_sources.update(st["source_refs"])
    sources = [sources_all[s] for s in sources_all if s in used_sources]
    if not sources:
        sources = [sources_all["sc"]]

    # --- disclaimers --------------------------------------------------------------------------------------
    disclaimers = [{"code": "not_investment_advice", "text": DISCLAIMER_NOT_ADVICE}]
    model_text = DISCLAIMER_MODEL
    if not positions:
        model_text += " По этому событию нет материалов аналитики Альфа-Банка — позиция банка не приводится."
    disclaimers.append({"code": "model_generated", "text": model_text})
    if any(s["kind"] == "hypothesis" for s in statements):
        disclaimers.append({"code": "hypothesis_present", "text": "Содержит гипотезы, требующие проверки."})
    completeness = float((ev.context.get("completeness") or {}).get("score", 1.0) or 0)
    if completeness < 0.8:
        missing = ", ".join((ev.context.get("completeness") or {}).get("missing") or []) or "часть признаков"
        disclaimers.append(
            {
                "code": "incomplete_data",
                "text": clip(
                    f"Часть данных о компании неизвестна ({missing}); оценка может быть неполной. Уточните профиль компании.",
                    300,
                ),
            }
        )
    if disagrees:
        disclaimers.append(
            {
                "code": "model_disagrees_with_bank",
                "text": "Вывод модели расходится с позицией аналитиков Альфа-Банка — см. пояснение.",
            }
        )

    title = clip(draft.title or str(e.get("title") or "Событие для компании"), 160)
    if len(title) < 5:
        title = clip(f"Событие: {e.get('title')}", 160)
    summary = clip(draft.summary, 600)
    if len(summary) < 20:
        summary = clip(f"{title}. {why[0]['text'] if why else ''}", 600)
    insight: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "insight_id": None,
        "company_id": ev.company_id,
        "event_id": str(e["event_id"]),
        "role_variant": ev.role,
        "trace_id": run_id,
        "language": "ru",
        "title": title,
        "summary": summary,
        "polarity": draft.polarity,
        "urgent": bool(e.get("urgent")),
        "event": event_obj,
        "why_it_matters": why[:6],
        "consequences": {
            "risks": cons(draft.risks)[:5],
            "opportunities": cons(draft.opportunities)[:5],
            "scenarios": scenarios[:3],
        },
        "impact": impact,
        "actions": actions,
        "bank_view": bank_view,
        "statements": statements,
        "relevance": relevance,
        "sources": sources,
        "disclaimers": disclaimers,
        "models_used": models_used,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=INSIGHT_TTL_DAYS)).isoformat(),
    }
    if not statements:
        # the contract requires at least one statement: the event itself, grounded in its news
        insight["statements"] = [
            {
                "id": "st1",
                "text": key_facts[0]["text"] if key_facts else title,
                "kind": "fact" if key_facts else "model_inference",
                "confidence": 0.9,
                "source_refs": key_facts[0]["source_refs"] if key_facts else [],
                "feature_refs": [],
            }
        ]
    insight["_assembler_notes"] = notes  # removed by the runner before returning (trace only)
    return insight


def strip_private(insight: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    notes = insight.pop("_assembler_notes", [])
    return insight, list(notes)


def valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value))
