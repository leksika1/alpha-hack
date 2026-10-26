"""Insight agent: plan -> gather -> draft -> assemble -> self-check -> critic -> revise -> final.

Every step is written to the backend trace via log_step (criteria 11-12). The LLM is optional at every point:
without it (no key, budget exhausted, allow_llm=false, provider errors, invalid JSON) the agent finishes with the
rule-based analyst, so the user always gets a grounded insight or an explicit `skipped`.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from alfa_ml import prompts, ranker2, semantic
from alfa_ml.agent import market
from alfa_ml.agent.assemble import assemble, strip_private
from alfa_ml.agent.checks import CheckResult, check_insight
from alfa_ml.agent.draft import Draft, parse_llm_draft
from alfa_ml.agent.evidence import Evidence
from alfa_ml.agent.plan import Plan, merge_llm_plan, rule_plan
from alfa_ml.agent.prompting import analyst_message, critic_message, planner_message, revision_message
from alfa_ml.agent.rules import rule_draft
from alfa_ml.config import Settings, get_settings
from alfa_ml.llm import BudgetExhausted, LLMClient, LLMError, pick_model
from alfa_ml.log import get_logger
from alfa_ml.profile import compute_exposure, normalize_event
from alfa_ml.ranker import RANKER_NAME, score_exposure
from alfa_ml.tools_client import Tools

log = get_logger(__name__)


@dataclass
class RunResult:
    status: str
    insight: dict[str, Any] | None = None
    skip_reason: str | None = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)  # local copy of the steps (tests, eval)


def _known_score(gold: int, prob: float) -> float:
    """A hand label of this very pair: 0 hides the card, 1 shows it, 2 shows it high; the model orders within."""
    if gold <= 0:
        return 0.0
    floor = ranker2.threshold() if gold == 1 else 0.95
    return round(max(prob, floor), 4)


# the analyst sometimes writes a "card" whose point is that nothing happens to the company
_NO_EFFECT = re.compile(
    r"не (затрагива|влия|повлия|отразит|касает|скажет)|без (прямого |заметного )?(влияния|последствий) для компании",
    re.IGNORECASE,
)


class Agent:
    def __init__(self, req: dict[str, Any], settings: Settings | None = None) -> None:
        self.req = req
        self.s = settings or get_settings()
        self.started = time.perf_counter()
        self.deadline_s = max(
            5.0, (int(req.get("deadline_ms") or 60000) - self.s.ml_deadline_margin_ms) / 1000
        )
        self.tools = Tools(req["tools"])
        options = req.get("options") or {}
        llm_url = req["tools"].get("llm_base_url")
        self.llm: LLMClient | None = None
        if self.s.ml_llm_enabled and options.get("allow_llm", True) and llm_url:
            self.llm = LLMClient(
                llm_url,
                req["tools"]["token"],
                req["tools"]["run_id"],
                self.s.ml_llm_timeout_s,
                no_think=self.s.ml_llm_no_think,
            )
        self.trace: list[dict[str, Any]] = []
        self.models: dict[str, set[str]] = {}

    # --- helpers ---------------------------------------------------------------------------------------
    def left(self) -> float:
        return self.deadline_s - (time.perf_counter() - self.started)

    async def step(self, step_type: str, name: str, status: str = "ok", **kw: Any) -> None:
        self.trace.append(
            {
                "step_type": step_type,
                "name": name,
                "status": status,
                **{k: v for k, v in kw.items() if k != "data"},
            }
        )
        await self.tools.step(step_type, name, status, **kw)

    def llm_ok(self, need_s: float = 12.0) -> bool:
        return self.llm is not None and self.llm.usable and self.left() > need_s

    async def ask(
        self, purpose: str, prompt: str, alias: str, user: str, max_tokens: int, name: str
    ) -> dict[str, Any] | None:
        assert self.llm is not None
        alias = pick_model(alias, self.s.ml_model_fast)  # a model that reasons too long -> the fast one
        timeout = max(5.0, min(self.s.ml_llm_timeout_s, self.left() - 3))
        try:
            answer = await self.llm.complete_json(
                alias=alias,
                purpose=purpose,
                system=prompts.system(prompt, self.req.get("role")),
                user=user,
                max_tokens=max_tokens,
                timeout_s=timeout,
            )
        except BudgetExhausted as exc:
            await self.step("llm_call", name, "skipped", error=f"budget_exhausted: {exc}"[:500])
            return None
        except LLMError as exc:
            await self.step("llm_call", name, "error", error=str(exc)[:500], model=alias)
            return None
        meta = self.llm.last
        self.models.setdefault(str(meta.get("model") or alias), set()).add(purpose)
        await self.step(
            "llm_call",
            name,
            model=meta.get("model"),
            tokens_in=meta.get("tokens_in"),
            tokens_out=meta.get("tokens_out"),
            latency_ms=meta.get("latency_ms"),
            data={k: meta.get(k) for k in ("reasoning_tokens", "finish_reason", "max_tokens")},
            input_preview=f"prompt {prompts.version(prompt)}; {len(user)} chars",
            output_preview=json.dumps(answer, ensure_ascii=False)[:1500],
        )
        return answer

    def usage(self) -> dict[str, int]:
        u = self.llm.usage if self.llm else None
        return {
            "llm_calls": u.llm_calls if u else 0,
            "tokens_in": u.tokens_in if u else 0,
            "tokens_out": u.tokens_out if u else 0,
            "latency_ms": int((time.perf_counter() - self.started) * 1000),
        }

    def models_used(self, draft_author: str) -> list[dict[str, str]]:
        out = [{"name": RANKER_NAME, "purpose": "scoring"}]
        if draft_author == "rules@1":
            out.append({"name": "rules@1", "purpose": "template"})
        for name, purposes in sorted(self.models.items()):
            for p in sorted(purposes):
                mapped = {"planning": "planning", "generation": "generation", "self_check": "self_check"}.get(
                    p
                )
                if mapped:
                    out.append({"name": name, "purpose": mapped})
        return out

    # --- pipeline --------------------------------------------------------------------------------------
    async def gather(self, ev: Evidence, plan: Plan) -> None:
        cid = ev.company_id
        for q in plan.analytics_queries:
            if q in ev.queries_done or len(ev.analytics) >= self.s.ml_max_analytics_hits:
                continue
            ev.queries_done.add(q)
            res = await self.tools.safe(
                "search_bank_analytics", {"query": q[:500], "limit": 3}, {"hits": []}, "retrieval"
            )
            ev.add_analytics(res.get("hits", [])[:3], q)
        for q in plan.news_queries:
            if q in ev.queries_done:
                continue
            ev.queries_done.add(q)
            res = await self.tools.safe(
                "search_news", {"query": q[:500], "limit": 4, "max_chars": 800}, {"hits": []}, "retrieval"
            )
            hits = [h for h in res.get("hits", []) if h.get("event_id") != ev.event.get("event_id")]
            ev.add_news(hits[:3], related=True)
        if plan.need_counterparties and ev.counterparties is None:
            ev.counterparties = await self.tools.safe(
                "get_counterparties", {"company_id": cid, "direction": "both", "limit": 10}, None
            )
        if plan.need_group and ev.group is None:
            ev.group = await self.tools.safe("get_group", {"company_id": cid, "members_limit": 10}, None)
        if not ev.products:
            res = await self.tools.safe("get_bank_products", {"company_id": cid}, {"products": []})
            ev.products = list(res.get("products", []))

    async def market_data(self, ev: Evidence) -> None:
        """Official Bank of Russia figures for rate/FX events and a check of the news figures against them."""
        if not self.tools.can("get_market_data"):
            return
        text = " ".join([str(ev.event.get("title") or ""), *(f"{n.title}. {n.text}" for n in ev.news[:3])])
        codes = market.wanted(ev.event, set(ev.exposure.items), ev.features, text)
        published = ev.news[0].published_at if ev.news else ev.event.get("first_seen_at")
        date_from, date_to = market.date_window(published)
        for code in codes:
            series = await self.tools.safe(
                "get_market_data",
                {"indicator": code, "date_from": date_from, "date_to": date_to},
                None,
                "retrieval",
            )
            off = market.official(series, published) if isinstance(series, dict) else None
            if off is None:
                continue
            ev.market.append(off)
            verdict = market.check(text, off)
            if verdict is not None:
                ev.market_checks.append(verdict)
        if ev.market_checks:
            bad = [c for c in ev.market_checks if c["mismatch"]]
            await self.step(
                "self_check",
                "market_check",
                "error" if bad else "ok",
                output_preview=(
                    "; ".join(
                        f"{c['indicator']}: в новости {c['mismatched_values']}, официально {c['official']} на {c['official_date']}"
                        for c in bad
                    )
                    or "цифры новости совпадают с официальными данными Банка России"
                ),
                data={"checks": ev.market_checks},
            )

    def merge_with_rules(self, llm: Draft, rules: Draft) -> Draft:
        """LLM text, deterministic safety net: borrow what the LLM omitted or got unverifiable."""
        borrowed: set[str] = set()
        if not llm.why_it_matters:
            llm.why_it_matters = rules.why_it_matters
            borrowed.update(c for r in rules.why_it_matters for c in r.claims)
        if not llm.key_facts:
            llm.key_facts = rules.key_facts
        if not llm.bank_positions and rules.bank_positions:
            llm.bank_positions = rules.bank_positions
            borrowed.update(c.id for c in rules.claims if c.kind == "bank_analytics")
        if not llm.risks and not llm.opportunities:
            llm.risks, llm.opportunities = rules.risks, rules.opportunities
            borrowed.update(c for x in rules.risks + rules.opportunities for c in x.claims)
        if not llm.scenarios:
            llm.scenarios = rules.scenarios
            borrowed.update(c for x in rules.scenarios for c in x.claims)
        if not llm.actions:
            llm.actions = rules.actions
        llm.quantitative = rules.quantitative  # numbers come only from deterministic calculation
        if not any("sm" in c.sources for c in llm.claims):
            borrowed.update(
                c.id for c in rules.claims if "sm" in c.sources
            )  # official Bank of Russia figures
        llm.claims += [c for c in rules.claims if c.id in borrowed]
        if not llm.title or len(llm.title) < 5:
            llm.title = rules.title
        if not llm.summary or len(llm.summary) < 20:
            llm.summary = rules.summary
        return llm

    async def build(self, draft: Draft, ev: Evidence, label: str) -> tuple[dict[str, Any], CheckResult]:
        insight = assemble(draft, ev, run_id=self.tools.run_id, models_used=self.models_used(draft.author))
        insight, notes = strip_private(insight)
        checks = check_insight(insight, ev)
        await self.step(
            "self_check",
            f"checks_{label}",
            "ok" if checks.ok else "error",
            output_preview=json.dumps(checks.as_dict(), ensure_ascii=False)[:2000],
            data={"checks": checks.as_dict(), "assembler_notes": notes[:30]},
        )
        return insight, checks

    async def run(self) -> RunResult:
        req = self.req
        cid, role = req["company_id"], req["role"]
        try:
            await self.step(
                "plan",
                "plan_insight",
                input_preview=f"company={cid} event={req['event_id']} role={role}",
                output_preview="get_company_context -> get_event -> exposure -> rule/LLM plan -> retrieval -> draft -> self_check -> critic -> final",
                data={"llm": self.llm is not None, "deadline_s": round(self.deadline_s, 1)},
            )
            context = await self.tools.timed("get_company_context", {"company_id": cid})
            event = await self.tools.timed("get_event", {"event_id": req["event_id"], "max_chars": 3000})
            event = normalize_event(event)
            features = context.get("features") or {}
            exposure = compute_exposure(features, event, cid)
            ev = Evidence(
                company_id=cid,
                role=role,
                context=context,
                event=event,
                exposure=exposure,
                candidate=req.get("candidate"),
            )
            ev.add_news((event.get("news") or [])[: self.s.ml_max_news])
            plan = rule_plan(event, features, exposure)
            pre = score_exposure(exposure, event, role)
            learned: dict[str, Any] | None = None
            if exposure.items and ranker2.available():
                try:
                    x = ranker2.pair_features(features, event, exposure, pre["score"])
                    prob, contrib = ranker2.predict(x)
                    concept = semantic.top_concept(features, event)
                    known = ranker2.known_label(features, event)
                    learned = {
                        "name": ranker2.NAME,
                        "score": round(prob, 4) if known is None else _known_score(known, prob),
                        "model_score": round(prob, 4),
                        "known_label": known,  # the same news was labelled by hand for this company: it wins
                        "threshold": ranker2.threshold(),
                        "top_features": ranker2.explain(contrib),
                        "concept": {"name": concept[0], "similarity": round(concept[1], 3)}
                        if concept
                        else None,
                    }
                    ev.learned = learned
                except Exception as exc:  # the learned ranker is an improvement, never a point of failure
                    log.warning("ranker2_failed", error=str(exc)[:300])
            await self.step(
                "plan",
                "rule_plan",
                input_preview=f"company={cid} event={req['event_id']} role={role}",
                output_preview="; ".join(plan.steps()),
                data={
                    "plan": plan.as_dict(),
                    "exposure": {k: round(v.value, 3) for k, v in exposure.items.items()},
                    "pre_score": pre["score"],
                    "event_screen": event.get("ml_screen"),  # ADR-ML-010: why channels were gated
                    "learned": learned,  # ADR-ML-011: ml-ranker@2 probability and its main reasons
                },
            )
            relevant = (
                learned["score"] >= learned["threshold"]
                if learned
                else pre["score"] >= self.s.ml_skip_threshold
            )
            if not exposure.items or not relevant:
                await self.step(
                    "final",
                    "skip_not_relevant",
                    "skipped",
                    output_preview=f"not relevant (rules {pre['score']}"
                    + (f", ml-ranker@2 {learned['score']} < {learned['threshold']})" if learned else ")"),
                )
                return RunResult("skipped", skip_reason="not_relevant", usage=self.usage(), trace=self.trace)
            if not ev.news:
                await self.step(
                    "final", "skip_insufficient_data", "skipped", output_preview="event without news texts"
                )
                return RunResult(
                    "skipped", skip_reason="insufficient_data", usage=self.usage(), trace=self.trace
                )
            if any("suspected_prompt_injection" in n.flags for n in ev.news) and all(
                not n.text for n in ev.news
            ):
                await self.step(
                    "final", "skip_unsafe", "skipped", output_preview="news text is only an injection attempt"
                )
                return RunResult(
                    "skipped", skip_reason="unsafe_content", usage=self.usage(), trace=self.trace
                )

            if self.llm is not None and pre["score"] < self.s.ml_llm_min_score:
                await self.step(
                    "plan",
                    "llm_budget_guard",
                    "skipped",
                    output_preview=f"pre-score {pre['score']} < {self.s.ml_llm_min_score}: rules@1 without LLM calls",
                )
                await self.llm.close()
                self.llm = None
            if self.s.ml_llm_planner and self.llm_ok(30):
                answer = await self.ask(
                    "planning",
                    "planner",
                    self.s.ml_model_planner,
                    planner_message(ev, plan),
                    1000,
                    "llm_plan",
                )
                if answer:
                    plan = merge_llm_plan(plan, answer)
                    await self.step(
                        "plan",
                        "llm_plan_merged",
                        output_preview="; ".join(plan.steps()),
                        data={"plan": plan.as_dict()},
                    )
            await self.gather(ev, plan)
            await self.market_data(ev)
            if not ev.analytics:
                # self-correction: broader query before concluding that the bank has no view on the topic
                q = str(event.get("title") or "")[:200]
                await self.step("retry", "analytics_broaden", output_preview=f"no analytics hits, retry: {q}")
                res = await self.tools.safe(
                    "search_bank_analytics", {"query": q, "limit": 3}, {"hits": []}, "retrieval"
                )
                ev.add_analytics(res.get("hits", [])[:3], q)

            rules = rule_draft(ev, prefix="r")
            rules_quants = [q.model_dump() for q in rules.quantitative]
            draft: Draft = rules
            if self.llm_ok(20):
                base_msg = analyst_message(ev, plan, rules_quants)
                answer = await self.ask(
                    "generation", "analyst", self.s.ml_model_analyst, base_msg, 4000, "llm_draft"
                )
                if answer is not None:
                    llm_draft = parse_llm_draft(
                        answer, author=self.llm.last.get("model", "llm") if self.llm else "llm"
                    )
                    says_no_effect = bool(_NO_EFFECT.search(f"{llm_draft.title} {llm_draft.summary}"))
                    if (not llm_draft.relevant or says_no_effect) and (
                        exposure.strongest is None or exposure.strongest.weight < 0.8
                    ):
                        await self.step(
                            "final",
                            "skip_llm_not_relevant",
                            "skipped",
                            output_preview=str(llm_draft.skip_reason)[:300],
                        )
                        return RunResult(
                            "skipped", skip_reason="not_relevant", usage=self.usage(), trace=self.trace
                        )
                    if says_no_effect:
                        # a card whose headline says "does not affect the company" is not shown as an insight;
                        # with a strong rule link the deterministic draft stays
                        await self.step(
                            "self_check", "llm_says_no_effect", "error", output_preview=llm_draft.title[:300]
                        )
                    else:
                        draft = self.merge_with_rules(llm_draft, rules)
            insight, checks = await self.build(draft, ev, "draft")

            iteration = 0
            while iteration < self.s.ml_max_iterations and draft.author != "rules@1" and self.llm_ok(20):
                issues = checks.blocking + checks.quality
                if not issues and self.s.ml_llm_critic:
                    verdict = await self.ask(
                        "self_check",
                        "critic",
                        self.s.ml_model_critic,
                        critic_message(ev, insight),
                        1500,
                        "llm_critic",
                    )
                    if verdict is None or verdict.get("verdict") != "revise":
                        break
                    issues = [i for i in verdict.get("issues", []) if isinstance(i, dict)][:8]
                    more = verdict.get("need_more_data") or {}
                    extra = Plan(
                        analytics_queries=[str(q) for q in (more.get("analytics_queries") or [])][:2],
                        news_queries=[str(q) for q in (more.get("news_queries") or [])][:1],
                        need_counterparties=bool(more.get("need_counterparties")),
                    )
                    if extra.analytics_queries or extra.news_queries or extra.need_counterparties:
                        await self.step("retry", "critic_more_data", output_preview="; ".join(extra.steps()))
                        await self.gather(ev, extra)
                if not issues or not self.llm_ok(15):
                    break
                iteration += 1
                await self.step(
                    "retry",
                    f"revise_{iteration}",
                    output_preview=json.dumps(issues, ensure_ascii=False)[:1500],
                    attempt=iteration + 1,
                )
                answer = await self.ask(
                    "generation",
                    "analyst",
                    self.s.ml_model_analyst,
                    revision_message(analyst_message(ev, plan, rules_quants), draft, issues),
                    4000,
                    f"llm_revise_{iteration}",
                )
                if answer is None:
                    break
                candidate = self.merge_with_rules(
                    parse_llm_draft(answer, author=draft.author), rule_draft(ev, prefix="r")
                )
                cand_insight, cand_checks = await self.build(candidate, ev, f"revision_{iteration}")
                if cand_checks.ok and len(cand_checks.quality) <= len(checks.quality):
                    draft, insight, checks = candidate, cand_insight, cand_checks
                if checks.ok and not checks.quality and not self.s.ml_llm_critic:
                    break

            if not checks.ok:
                await self.step(
                    "retry", "fallback_rules", output_preview="blocking problems remain; using rules@1 draft"
                )
                draft = rule_draft(ev, prefix="r")
                insight, checks = await self.build(draft, ev, "fallback")
            if not checks.ok:
                detail = "; ".join(f"{p['code']}: {p['detail']}" for p in checks.blocking)[:500]
                await self.step("error", "invalid_insight", "error", error=detail)
                return RunResult("failed", error=detail, usage=self.usage(), trace=self.trace)
            await self.step(
                "final",
                "insight_ready",
                output_preview=insight["title"],
                data={
                    "author": draft.author,
                    "impact": insight["impact"]["level"],
                    "relevance": insight["relevance"]["score"],
                    "quality": checks.quality,
                },
            )
            return RunResult("completed", insight=insight, usage=self.usage(), trace=self.trace)
        except Exception as exc:
            log.exception("generation_failed", run_id=self.tools.run_id)
            await self.step("error", "generation_failed", "error", error=f"{type(exc).__name__}: {exc}"[:500])
            return RunResult(
                "failed", error=f"{type(exc).__name__}: {exc}"[:500], usage=self.usage(), trace=self.trace
            )
        finally:
            await self.tools.close()
            if self.llm:
                await self.llm.close()
