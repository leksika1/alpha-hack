"""Evidence pack: everything the agent collected through the Tools API, with stable local source IDs.

Source IDs used in prompts and in the insight: s1..sN news of the event, sn1.. related news, sa1.. bank
analytics chunks, sc company data, sk counterparties, sg group. The LLM may only cite these IDs.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from alfa_ml.profile import ExposureResult

_INJECTION = re.compile(
    r"(ignore (all|previous|the above)|disregard .*instructions|system prompt|you are now|"
    r"игнорируй|забудь (все|предыдущ)|новые инструкции|ты теперь|системн\w* промпт|выведи .*промпт)",
    re.I,
)


def strip_injection(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    return " ".join(s for s in sentences if not _INJECTION.search(s)).strip()


def clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip(" ,.;:") + "…"


def is_demo_material(hit: dict[str, Any]) -> bool:
    return str(hit.get("doc_title", "")).startswith("ДЕМО")


def analytics_publisher(hit: dict[str, Any]) -> str:
    """Synthetic demo PDFs must never be presented as real Alfa-Bank analytics."""
    return "Демо-материал (синтетика), не аналитика Альфа-Банка" if is_demo_material(hit) else "Альфа-Банк"


@dataclass
class NewsRef:
    sid: str
    news_id: str
    title: str
    url: str | None
    publisher: str | None
    published_at: str | None
    text: str
    flags: list[str] = field(default_factory=list)


@dataclass
class AnalyticsRef:
    sid: str
    hit: dict[str, Any]
    query: str


@dataclass
class Evidence:
    company_id: str
    role: str
    context: dict[str, Any]
    event: dict[str, Any]
    exposure: ExposureResult
    candidate: dict[str, Any] | None = None
    news: list[NewsRef] = field(default_factory=list)
    related_news: list[NewsRef] = field(default_factory=list)
    analytics: list[AnalyticsRef] = field(default_factory=list)
    counterparties: dict[str, Any] | None = None
    group: dict[str, Any] | None = None
    products: list[dict[str, Any]] = field(default_factory=list)
    queries_done: set[str] = field(default_factory=set)
    learned: dict[str, Any] | None = None  # ml-ranker@2 verdict (score, threshold, top features), ADR-ML-011
    market: list[Any] = field(
        default_factory=list
    )  # agent.market.Official: Bank of Russia figures (source sm)
    market_checks: list[dict[str, Any]] = field(default_factory=list)  # news figures vs the official ones

    @property
    def features(self) -> dict[str, Any]:
        f: dict[str, Any] = self.context.get("features") or {}
        return f

    @property
    def feature_paths(self) -> frozenset[str]:
        return frozenset(self.features)

    @property
    def product_ids(self) -> set[str]:
        return {str(p.get("product_id") or p.get("id")) for p in self.products}

    def add_news(self, items: list[dict[str, Any]], related: bool = False) -> None:
        target = self.related_news if related else self.news
        known = {n.news_id for n in self.news + self.related_news}
        for n in items:
            nid = str(n.get("news_id"))
            if nid in known:
                continue
            body = n.get("body") or n.get("snippet") or {}
            content = body.get("content", "") if isinstance(body, dict) else str(body)
            prefix = "sn" if related else "s"
            target.append(
                NewsRef(
                    sid=f"{prefix}{len(target) + 1}",
                    news_id=nid,
                    title=str(n.get("title", "")),
                    url=n.get("url"),
                    publisher=n.get("publisher") or n.get("source"),
                    published_at=n.get("published_at"),
                    text=strip_injection(content),
                    flags=list(body.get("flags", [])) if isinstance(body, dict) else [],
                )
            )
            known.add(nid)

    def add_analytics(self, hits: list[dict[str, Any]], query: str) -> int:
        known = {a.hit["chunk_id"] for a in self.analytics}
        added = 0
        for h in hits:
            if h.get("chunk_id") in known:
                continue
            self.analytics.append(AnalyticsRef(sid=f"sa{len(self.analytics) + 1}", hit=h, query=query))
            known.add(h["chunk_id"])
            added += 1
        return added

    # --- sources -------------------------------------------------------------------------------------------
    def all_sources(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for n in self.news + self.related_news:
            out[n.sid] = {
                "id": n.sid,
                "type": "news",
                "ref_id": n.news_id,
                "url": n.url,
                "title": clip(n.title, 300) or "Новость",
                "publisher": n.publisher,
                "published_at": n.published_at,
                "page": None,
            }
        for a in self.analytics:
            h = a.hit
            out[a.sid] = {
                "id": a.sid,
                "type": "bank_analytics",
                "ref_id": h["chunk_id"],
                "url": None,
                "title": str(h.get("doc_title") or "Аналитика Альфа-Банка"),
                "publisher": analytics_publisher(h),
                "published_at": f"{h['doc_date']}T00:00:00Z" if h.get("doc_date") else None,
                "page": h.get("page"),
            }
        if self.market:
            latest = max(o.day for o in self.market)
            out["sm"] = {
                "id": "sm",
                "type": "external_stat",
                "ref_id": ",".join(o.code for o in self.market),
                "url": self.market[0].url,
                "title": "Банк России: " + "; ".join(o.title for o in self.market),
                "publisher": "Банк России",
                "published_at": f"{latest[:10]}T00:00:00Z",
                "page": None,
            }
        out["sc"] = {
            "id": "sc",
            "type": "company_data",
            "ref_id": "company_context",
            "url": None,
            "title": "Данные о компании в Альфа-Банке",
            "publisher": None,
            "published_at": None,
            "page": None,
        }
        if self.counterparties and self.counterparties.get("items"):
            out["sk"] = {
                "id": "sk",
                "type": "company_data",
                "ref_id": "counterparties",
                "url": None,
                "title": "Платежи с контрагентами (без самопереводов)",
                "publisher": None,
                "published_at": None,
                "page": None,
            }
        if self.group and self.group.get("size", 0) > 1:
            out["sg"] = {
                "id": "sg",
                "type": "company_data",
                "ref_id": "group",
                "url": None,
                "title": "Группа компаний",
                "publisher": None,
                "published_at": None,
                "page": None,
            }
        return out

    def source_text(self, sid: str) -> str:
        for n in self.news + self.related_news:
            if n.sid == sid:
                return f"{n.title}. {n.text}"
        for a in self.analytics:
            if a.sid == sid:
                return str(a.hit.get("text", ""))
        return ""

    def analytics_by_sid(self, sid: str) -> dict[str, Any] | None:
        return next((a.hit for a in self.analytics if a.sid == sid), None)

    # --- prompt rendering ----------------------------------------------------------------------------------
    def render_news(self, max_chars: int = 1400) -> str:
        parts = []
        for n in self.news:
            parts.append(
                f'<untrusted_content source="news" id="{n.sid}" publisher="{n.publisher}" '
                f'date="{n.published_at}">\n{clip(n.title, 300)}\n{clip(n.text, max_chars)}\n</untrusted_content>'
            )
        for n in self.related_news[:3]:
            parts.append(
                f'<untrusted_content source="related_news" id="{n.sid}" date="{n.published_at}">\n'
                f"{clip(n.title, 300)}\n{clip(n.text, 500)}\n</untrusted_content>"
            )
        return "\n".join(parts) or "(нет текстов новостей)"

    def render_analytics(self, max_chars: int = 900) -> str:
        if not self.analytics:
            return "(по запросам ничего не найдено — позицию банка не приводить)"
        parts = []
        for a in self.analytics:
            h = a.hit
            demo = " [ДЕМО-материал, не аналитика банка]" if is_demo_material(h) else ""
            parts.append(
                f"[{a.sid}] «{h.get('doc_title')}», {h.get('doc_date') or 'дата неизвестна'}, с. {h.get('page')}"
                f"{demo}\n{clip(str(h.get('text', '')), max_chars)}"
            )
        return "\n\n".join(parts)

    def render_market(self) -> str:
        if not self.market:
            return "(не требуются для этого события)"
        lines = [f"- {o.phrase()}" for o in self.market]
        for c in self.market_checks:
            if c.get("mismatch"):
                lines.append(
                    f"- ВНИМАНИЕ: в новости {c['indicator']} = {c['mismatched_values']}, официальное значение "
                    f"на {c['official_date']} — {c['official']}. Приводи официальное значение и отметь расхождение."
                )
        return "\n".join(lines)

    def render_counterparties(self, limit: int = 8) -> str:
        if not self.counterparties or not self.counterparties.get("items"):
            return "(данных о контрагентах нет)"
        t = self.counterparties.get("totals") or {}
        lines = [
            f"Неизвестные контрагенты: входящие {t.get('unknown_share_in')}, исходящие {t.get('unknown_share_out')}"
            " (их профиль не восстанавливается)."
        ]
        for cp in self.counterparties["items"][:limit]:
            hyp = cp.get("relation_hypothesis") or {}
            hyp_txt = f", гипотеза: {hyp.get('label')} (уверенность {hyp.get('confidence')})" if hyp else ""
            lines.append(
                f"- {'поставщик/получатель' if cp.get('direction') == 'out' else 'плательщик'} "
                f"{cp.get('counterparty_id')}: доля {cp.get('share')}, отрасль {cp.get('okved_name')}, "
                f"теги {cp.get('industry_tags')}, регион {cp.get('region')}, своя группа {cp.get('same_group')}"
                f"{hyp_txt}"
            )
        return "\n".join(lines)

    def render_group(self) -> str:
        g = self.group
        if not g or g.get("size", 0) <= 1:
            return "(компания не входит в группу или данных нет)"
        members = ", ".join(
            f"{m.get('company_id')} ({clip(str(m.get('okved_name')), 60)})"
            for m in (g.get("members") or [])[:8]
        )
        return (
            f"Группа {g.get('group_id')}: {g.get('size')} юрлиц, сегмент {g.get('segment')}, "
            f"отрасли {g.get('industries')}. Участники: {members}"
        )

    def render_products(self) -> str:
        if not self.products:
            return "(каталог недоступен — product_id не указывать)"
        return "\n".join(
            f"- {p.get('product_id') or p.get('id')}: {p.get('name')} (теги {p.get('tags')})"
            for p in self.products
        )

    def render_exposure(self) -> str:
        if not self.exposure.items:
            return "(прямой подверженности по данным не найдено)"
        return "\n".join(
            f"- {e.name}: сила {e.value:.2f}, признаки {e.features} — {e.reason}"
            for e in self.exposure.ordered()
        )
