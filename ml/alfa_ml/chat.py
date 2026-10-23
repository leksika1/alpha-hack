"""Questions to the analytics (/v1/chat, CJM stage 06): answer with sources, assumptions and limits.

Context: the insight (if asked from a card), company features, bank analytics and news found for the question,
group / counterparties for "another slice". The LLM writes the answer as JSON (prompts/chat.md); without LLM a
deterministic answer is composed from the same evidence. Unsupported facts are downgraded, never shown as facts.
"""

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from alfa_ml import prompts
from alfa_ml.agent.evidence import analytics_publisher, strip_injection
from alfa_ml.agent.textutil import best_sentences, clip
from alfa_ml.config import Settings, get_settings
from alfa_ml.contracts import DISCLAIMER_NOT_ADVICE
from alfa_ml.llm import LLMClient, LLMError
from alfa_ml.log import get_logger
from alfa_ml.profile import company_card
from alfa_ml.tools_client import Tools

log = get_logger(__name__)

GROUP_WORDS = re.compile(r"юрлиц|групп|дочерн|холдинг|другой компан", re.I)
CP_WORDS = re.compile(r"контрагент|поставщик|покупател|клиент[аы]? компании", re.I)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatContext:
    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.texts: dict[str, str] = {}

    def add(self, source: dict[str, Any], text: str = "") -> str:
        self.sources[source["id"]] = source
        if text:
            self.texts[source["id"]] = text
        return str(source["id"])


async def chat_stream(req: dict[str, Any], settings: Settings | None = None) -> AsyncIterator[str]:
    s = settings or get_settings()
    started = time.perf_counter()
    tools = Tools(req["tools"])
    llm: LLMClient | None = None
    if (
        s.ml_llm_enabled
        and req["tools"].get("llm_base_url")
        and (req.get("options") or {}).get("allow_llm", True)
    ):
        llm = LLMClient(
            req["tools"]["llm_base_url"], req["tools"]["token"], req["tools"]["run_id"], s.ml_llm_timeout_s
        )
    question = clip(str(req["messages"][-1].get("content", "")), 1000)
    history = [m for m in req["messages"][:-1] if m.get("role") in {"user", "assistant"}][-6:]
    insight = req.get("insight") or None
    intent = req.get("intent") or "free"
    cid = req["company_id"]
    ctx = ChatContext()
    try:
        await tools.step(
            "plan",
            "plan_answer",
            input_preview=question[:300],
            output_preview=f"intent={intent}; insight={'yes' if insight else 'no'}",
        )
        if insight:
            for src in insight.get("sources", [])[:8]:
                ctx.add(dict(src))
        yield sse("step", {"type": "retrieval", "name": "get_company_context"})
        context = await tools.safe("get_company_context", {"company_id": cid}, {"features": {}})
        features = context.get("features") or {}
        ctx.add(
            {
                "id": "qc",
                "type": "company_data",
                "ref_id": "company_context",
                "url": None,
                "title": "Данные о компании в Альфа-Банке",
                "publisher": None,
                "published_at": None,
                "page": None,
            }
        )

        query = question if not insight else f"{question} {insight.get('event', {}).get('title', '')}"
        yield sse("step", {"type": "retrieval", "name": "search_bank_analytics"})
        hits = (
            await tools.safe(
                "search_bank_analytics", {"query": query[:500], "limit": 4}, {"hits": []}, "retrieval"
            )
        ).get("hits", [])
        for i, h in enumerate(hits[:4], start=1):
            ctx.add(
                {
                    "id": f"qa{i}",
                    "type": "bank_analytics",
                    "ref_id": h["chunk_id"],
                    "url": None,
                    "title": str(h.get("doc_title") or "Аналитика"),
                    "publisher": analytics_publisher(h),
                    "published_at": f"{h['doc_date']}T00:00:00Z" if h.get("doc_date") else None,
                    "page": h.get("page"),
                },
                str(h.get("text", "")),
            )
        news: list[dict[str, Any]] = []
        if intent in {"other_slice", "free", "source"} or not insight:
            yield sse("step", {"type": "retrieval", "name": "search_news"})
            news = (
                await tools.safe(
                    "search_news",
                    {"query": question[:500], "limit": 3, "max_chars": 800},
                    {"hits": []},
                    "retrieval",
                )
            ).get("hits", [])
            for i, n in enumerate(news[:3], start=1):
                snippet = n.get("snippet") or {}
                ctx.add(
                    {
                        "id": f"qn{i}",
                        "type": "news",
                        "ref_id": n["news_id"],
                        "url": n.get("url"),
                        "title": str(n.get("title") or "Новость"),
                        "publisher": n.get("source"),
                        "published_at": n.get("published_at"),
                        "page": None,
                    },
                    strip_injection(
                        str(snippet.get("content", "") if isinstance(snippet, dict) else snippet)
                    ),
                )
        group = None
        if intent == "other_slice" or GROUP_WORDS.search(question):
            yield sse("step", {"type": "retrieval", "name": "get_group"})
            group = await tools.safe("get_group", {"company_id": cid, "members_limit": 10}, None)
            if group and group.get("size", 0) > 1:
                ctx.add(
                    {
                        "id": "qg",
                        "type": "company_data",
                        "ref_id": "group",
                        "url": None,
                        "title": "Группа компаний",
                        "publisher": None,
                        "published_at": None,
                        "page": None,
                    }
                )
        cps = None
        if CP_WORDS.search(question):
            yield sse("step", {"type": "retrieval", "name": "get_counterparties"})
            cps = await tools.safe(
                "get_counterparties", {"company_id": cid, "direction": "both", "limit": 8}, None
            )
            if cps and cps.get("items"):
                ctx.add(
                    {
                        "id": "qk",
                        "type": "company_data",
                        "ref_id": "counterparties",
                        "url": None,
                        "title": "Платежи с контрагентами (без самопереводов)",
                        "publisher": None,
                        "published_at": None,
                        "page": None,
                    }
                )

        answer: dict[str, Any] | None = None
        model_name = None
        if llm is not None and llm.usable and time.perf_counter() - started < 60:
            yield sse("step", {"type": "llm_call", "name": "answer"})
            user = _llm_message(question, history, insight, features, context, ctx, group, cps, intent)
            try:
                answer = await llm.complete_json(
                    alias=s.ml_model_chat,
                    purpose="chat",
                    system=prompts.system("chat", req.get("role")),
                    user=user,
                    max_tokens=900,
                    timeout_s=min(s.ml_llm_timeout_s, 40),
                )
                model_name = llm.last.get("model")
                await tools.step(
                    "llm_call",
                    "chat_answer",
                    model=model_name,
                    tokens_in=llm.last.get("tokens_in"),
                    tokens_out=llm.last.get("tokens_out"),
                    latency_ms=llm.last.get("latency_ms"),
                    output_preview=json.dumps(answer, ensure_ascii=False)[:1500],
                )
            except LLMError as exc:
                await tools.step("llm_call", "chat_answer", "error", error=str(exc)[:500])
                answer = None
        final = (
            _final_from_llm(answer, ctx)
            if answer and str(answer.get("answer", "")).strip()
            else _rule_answer(question, insight, ctx, features, group, cps)
        )
        await tools.step(
            "self_check",
            "chat_check",
            output_preview=f"statements={len(final['statements'])}, sources={len(final['sources'])}, author={model_name or 'rules@1'}",
        )
        text = final["answer"]
        for i in range(0, len(text), 80):
            yield sse("token", {"delta": text[i : i + 80]})
        for src in final["sources"]:
            yield sse("source", src)
        await tools.step("final", "answer_ready", output_preview=text[:300])
        yield sse("done", final)
    except Exception as exc:
        log.exception("chat_failed", run_id=tools.run_id)
        yield sse(
            "error",
            {
                "type": "https://alfa-future.local/problems/ml-error",
                "title": "Answer failed",
                "status": 500,
                "detail": f"{type(exc).__name__}: {exc}"[:300],
            },
        )
    finally:
        await tools.close()
        if llm:
            await llm.close()


def _llm_message(
    question: str,
    history: list[dict[str, Any]],
    insight: dict[str, Any] | None,
    features: dict[str, Any],
    context: dict[str, Any],
    ctx: ChatContext,
    group: Any,
    cps: Any,
    intent: str,
) -> str:
    parts = [f"Намерение: {intent}", f"Вопрос: {question}"]
    if history:
        parts.append(
            "История:\n" + "\n".join(f"{m['role']}: {clip(str(m.get('content', '')), 400)}" for m in history)
        )
    if insight:
        digest = {
            "title": insight.get("title"),
            "summary": insight.get("summary"),
            "why": [r.get("text") for r in insight.get("why_it_matters", [])],
            "risks": [r.get("text") for r in insight.get("consequences", {}).get("risks", [])],
            "opportunities": [
                r.get("text") for r in insight.get("consequences", {}).get("opportunities", [])
            ],
            "bank_view": [p.get("text") for p in insight.get("bank_view", {}).get("positions", [])],
            "statements": [
                {"kind": s.get("kind"), "text": s.get("text"), "sources": s.get("source_refs")}
                for s in insight.get("statements", [])
            ][:12],
        }
        parts.append("Инсайт:\n" + json.dumps(digest, ensure_ascii=False))
    parts.append("Признаки компании:\n" + company_card(features, context.get("completeness"), limit=40))
    for sid, src in ctx.sources.items():
        body = ctx.texts.get(sid, "")
        demo = (
            " [ДЕМО-материал]"
            if src.get("type") == "bank_analytics" and str(src.get("title", "")).startswith("ДЕМО")
            else ""
        )
        if body:
            parts.append(
                f'<untrusted_content id="{sid}" type="{src["type"]}" title="{clip(str(src["title"]), 100)}"{demo}>\n{clip(body, 900)}\n</untrusted_content>'
            )
        else:
            parts.append(f"[{sid}] {src['type']}: {clip(str(src['title']), 120)}")
    if group and group.get("size", 0) > 1:
        members = "; ".join(
            f"{m.get('company_id')} — {clip(str(m.get('okved_name')), 60)}, {m.get('region')}"
            for m in group.get("members", [])[:10]
        )
        parts.append(
            f"Группа (qg): {group.get('size')} юрлиц, сегмент {group.get('segment')}. Участники: {members}"
        )
    if cps and cps.get("items"):
        lines = [
            f"{c['counterparty_id']}: {'исходящие' if c['direction'] == 'out' else 'входящие'} {c['share']:.1%}, {c.get('okved_name')}"
            for c in cps["items"][:8]
        ]
        parts.append("Контрагенты (qk):\n" + "\n".join(lines))
    parts.append("Допустимые id источников: " + ", ".join(ctx.sources))
    return "\n\n".join(parts)


def _final_from_llm(answer: dict[str, Any], ctx: ChatContext) -> dict[str, Any]:
    statements = []
    used: list[str] = []
    for i, c in enumerate(answer.get("claims") or [], start=1):
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        kind = str(c.get("kind") or "model_inference")
        if kind not in {"fact", "bank_analytics", "model_inference", "hypothesis"}:
            kind = "model_inference"
        refs = [r for r in (c.get("sources") or []) if isinstance(r, str) and r in ctx.sources]
        if kind == "bank_analytics" and not any(ctx.sources[r]["type"] == "bank_analytics" for r in refs):
            kind = "model_inference"
        if kind == "fact" and not refs:
            kind = "model_inference"
        try:
            conf = max(0.0, min(1.0, float(c.get("confidence", 0.6))))
        except (TypeError, ValueError):
            conf = 0.6
        statements.append(
            {
                "id": f"st{i}",
                "text": clip(str(c["text"]), 600),
                "kind": kind,
                "confidence": conf,
                "source_refs": refs,
            }
        )
        used += [r for r in refs if r not in used]
        if len(statements) >= 10:
            break
    text = clip(str(answer.get("answer", "")), 3000)
    missing = [str(x) for x in (answer.get("missing_data") or []) if isinstance(x, str) and x.strip()][:3]
    if missing:
        text += " Для более точного ответа не хватает данных: " + "; ".join(missing) + "."
    if not statements:
        statements.append(
            {
                "id": "st1",
                "text": clip(text, 600),
                "kind": "model_inference",
                "confidence": 0.5,
                "source_refs": [],
            }
        )
    sources = [ctx.sources[s] for s in used] or [ctx.sources[s] for s in list(ctx.sources)[:3]]
    return {
        "answer": text,
        "sources": sources,
        "statements": statements,
        "disclaimers": [{"code": "not_investment_advice", "text": DISCLAIMER_NOT_ADVICE}],
    }


def _rule_answer(
    question: str,
    insight: dict[str, Any] | None,
    ctx: ChatContext,
    features: dict[str, Any],
    group: Any,
    cps: Any,
) -> dict[str, Any]:
    parts: list[str] = []
    statements: list[dict[str, Any]] = []
    used: list[str] = []

    def st(text: str, kind: str, conf: float, refs: list[str]) -> None:
        statements.append(
            {
                "id": f"st{len(statements) + 1}",
                "text": clip(text, 600),
                "kind": kind,
                "confidence": conf,
                "source_refs": refs,
            }
        )
        used.extend(r for r in refs if r not in used)

    if insight:
        why = (insight.get("why_it_matters") or [{}])[0].get("text")
        cons = (insight.get("consequences", {}).get("risks") or []) + (
            insight.get("consequences", {}).get("opportunities") or []
        )
        if why:
            parts.append(f"По инсайту «{clip(str(insight.get('title')), 120)}»: {why}")
        if cons:
            parts.append(f"Ключевое последствие: {cons[0]['text']}")
            refs = [s["id"] for s in insight.get("sources", [])[:1] if s["id"] in ctx.sources]
            st(cons[0]["text"], "model_inference", 0.6, refs)
    if group and group.get("size", 0) > 1:
        members = group.get("members") or []
        industries = ", ".join(f"{k} ({v})" for k, v in list((group.get("industries") or {}).items())[:4])
        parts.append(
            f"В группе {group.get('size')} юрлиц (сегмент {group.get('segment')}); отрасли участников: {industries or 'нет данных'}. Влияние на отдельное юрлицо зависит от его отрасли и продуктов: оцените участников из затронутой отрасли в первую очередь."
        )
        st(
            f"Группа компании включает {group.get('size')} юрлиц; показаны {len(members)}.",
            "fact",
            0.95,
            ["qg"] if "qg" in ctx.sources else ["qc"],
        )
    if cps and cps.get("items"):
        top = cps["items"][:3]
        txt = "; ".join(
            f"{c['counterparty_id']} ({clip(str(c.get('okved_name') or 'отрасль неизвестна'), 50)}, {c['share']:.0%} {'исходящих' if c['direction'] == 'out' else 'входящих'})"
            for c in top
        )
        parts.append(f"Крупнейшие контрагенты по платежам: {txt}.")
        st(f"Крупнейшие контрагенты компании: {txt}.", "fact", 0.9, ["qk"] if "qk" in ctx.sources else ["qc"])
    bank = [sid for sid, s in ctx.sources.items() if s["type"] == "bank_analytics" and sid.startswith("qa")]
    for sid in bank[:2]:
        best = best_sentences(ctx.texts.get(sid, ""), question, n=2)
        if not best:
            continue
        label = (
            "Демо-материал (не аналитика Альфа-Банка)"
            if str(ctx.sources[sid]["title"]).startswith("ДЕМО")
            else "Аналитики Альфа-Банка"
        )
        text = f"{label} («{clip(str(ctx.sources[sid]['title']), 80)}», с. {ctx.sources[sid].get('page')}): {' '.join(best)}"
        parts.append(text)
        st(" ".join(best), "bank_analytics", 0.85, [sid])
    if not bank:
        parts.append(
            "В аналитике Альфа-Банка нет материалов по этому вопросу; ответ основан на данных компании и новостях."
        )
    news = [sid for sid, s in ctx.sources.items() if s["type"] == "news" and sid.startswith("qn")]
    if news:
        parts.append(
            "Связанные новости: " + "; ".join(clip(str(ctx.sources[s]["title"]), 100) for s in news[:3]) + "."
        )
        used.extend(s for s in news[:3] if s not in used)
    if not parts:
        parts.append(
            "Недостаточно данных для ответа. Уточните вопрос или откройте вопрос из карточки инсайта."
        )
    parts.append(
        "Чтобы оценить эффект точнее, уточните в профиле компании суммы и валюту долга, долю ключевых поставщиков и покупателей."
    )
    if not statements:
        st(parts[0], "model_inference", 0.5, [])
    sources = [ctx.sources[s] for s in used if s in ctx.sources] or [ctx.sources["qc"]]
    return {
        "answer": clip(" ".join(parts), 3000),
        "sources": sources,
        "statements": statements,
        "disclaimers": [{"code": "not_investment_advice", "text": DISCLAIMER_NOT_ADVICE}],
    }
