"""User messages for the planner, analyst and critic. Everything external is wrapped as untrusted data."""

import json
from typing import Any

from alfa_ml.agent.draft import Draft
from alfa_ml.agent.evidence import Evidence
from alfa_ml.agent.plan import Plan
from alfa_ml.agent.textutil import clip
from alfa_ml.profile import company_card

ROLE_NAMES = {
    "owner": "собственник",
    "ceo": "генеральный директор",
    "cfo": "финансовый директор",
    "chief_accountant": "главный бухгалтер",
    "treasurer": "казначей",
    "other": "руководитель",
}


def _event_header(ev: Evidence) -> str:
    e = ev.event
    tags = e.get("tags") or {}
    compact = {
        k: v
        for k, v in tags.items()
        if v and k in {"industry_tags", "countries", "commodities", "regions", "currencies", "numbers"}
    }
    return (
        f"Тип: {e.get('event_type')}; направление: {e.get('direction', 'none')}; срочное: {bool(e.get('urgent'))}; "
        f"впервые: {e.get('first_seen_at')}\nЗаголовок события: {clip(str(e.get('title', '')), 300)}\n"
        f"Теги (автоматически): {json.dumps(compact, ensure_ascii=False)}"
    )


def planner_message(ev: Evidence, plan: Plan) -> str:
    return (
        f"Читатель: {ROLE_NAMES.get(ev.role, ev.role)}.\n\n## Событие\n{_event_header(ev)}\n\n"
        f"{ev.render_news(max_chars=700)}\n\n## Компания (признаки)\n{company_card(ev.features, ev.context.get('completeness'), limit=35)}\n\n"
        f"## Подверженность по правилам\n{ev.render_exposure()}\n\n"
        f"## Черновой план (правила)\n{json.dumps(plan.as_dict(), ensure_ascii=False)}"
    )


def sources_index(ev: Evidence) -> str:
    lines = []
    for sid, s in ev.all_sources().items():
        lines.append(f"- {sid}: {s['type']} — {clip(str(s['title']), 120)}")
    return "\n".join(lines)


def analyst_message(ev: Evidence, plan: Plan, quants: list[dict[str, Any]]) -> str:
    quant_txt = (
        "\n".join(f"- {q['metric']} = {q['value']} {q['unit']} ({q['basis']})" for q in quants) or "(нет)"
    )
    return (
        f"Читатель: {ROLE_NAMES.get(ev.role, ev.role)}. Компания: {ev.company_id}.\n"
        f"Фокус: {plan.focus}\nГипотезы для проверки: {'; '.join(plan.hypotheses) or '—'}\n\n"
        f"## Событие\n{_event_header(ev)}\n\n## Тексты новостей (s…, sn…)\n{ev.render_news()}\n\n"
        f"## Признаки компании (единственные допустимые feature-пути)\n{company_card(ev.features, ev.context.get('completeness'))}\n\n"
        f"## Подверженность компании событию (рассчитано по данным)\n{ev.render_exposure()}\n\n"
        f"## Контрагенты (sk)\n{ev.render_counterparties()}\n\n## Группа (sg)\n{ev.render_group()}\n\n"
        f"## Аналитика Альфа-Банка (sa…)\n{ev.render_analytics()}\n\n"
        f"## Официальные данные Банка России (sm)\n{ev.render_market()}\n\n"
        f"## Рассчитанные показатели\n{quant_txt}\n\n## Каталог продуктов банка\n{ev.render_products()}\n\n"
        f"## Допустимые источники\n{sources_index(ev)}"
    )


def draft_digest(insight: dict[str, Any]) -> dict[str, Any]:
    """Compact view of an assembled insight for the critic and for revision prompts."""
    return {
        "title": insight["title"],
        "summary": insight["summary"],
        "polarity": insight["polarity"],
        "impact": {"level": insight["impact"]["level"], "score": insight["impact"]["score"]},
        "why_it_matters": [
            {"text": r["text"], "features": r["feature_refs"]} for r in insight["why_it_matters"]
        ],
        "statements": [
            {"id": s["id"], "kind": s["kind"], "text": s["text"], "sources": s["source_refs"]}
            for s in insight["statements"]
        ],
        "risks": [r["text"] for r in insight["consequences"]["risks"]],
        "opportunities": [o["text"] for o in insight["consequences"]["opportunities"]],
        "bank_positions": [
            {"source": p["source_ref"], "text": p["text"], "quote": p["quote"]}
            for p in insight["bank_view"]["positions"]
        ],
        "actions": [a["text"] for a in insight["actions"]],
    }


def critic_message(ev: Evidence, insight: dict[str, Any]) -> str:
    return (
        f"Читатель: {ROLE_NAMES.get(ev.role, ev.role)}.\n\n## Черновик инсайта\n"
        f"{json.dumps(draft_digest(insight), ensure_ascii=False)}\n\n## Данные\n### Новости\n{ev.render_news(max_chars=900)}\n\n"
        f"### Аналитика банка\n{ev.render_analytics(max_chars=700)}\n\n### Признаки компании\n"
        f"{company_card(ev.features, ev.context.get('completeness'), limit=40)}\n\n### Контрагенты\n{ev.render_counterparties(limit=5)}"
    )


def revision_message(base: str, previous: Draft | dict[str, Any], issues: list[dict[str, str]]) -> str:
    prev = previous.model_dump(exclude={"author"}) if isinstance(previous, Draft) else previous
    issues_txt = "\n".join(
        f"- [{i.get('code', i.get('field', '?'))}] {i.get('detail') or i.get('problem', '')} {i.get('fix', '')}".strip()
        for i in issues
    )
    return (
        f"{base}\n\n## Предыдущий черновик\n{json.dumps(prev, ensure_ascii=False)[:6000]}\n\n"
        f"## Замечания контролёра — исправь их и верни полный исправленный JSON\n{issues_txt}"
    )
