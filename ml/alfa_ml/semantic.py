"""Semantic layer of the relevance model (ADR-ML-011): multilingual embeddings + a cross-encoder reranker.

Why: the rule exposure (profile.py) depends on the backend's keyword tags, and keyword rules break on new
wording ("медики" -> copper, "прогноз по средней ставке" is not "ключевая ставка"). The semantic layer reads the
event the way a person does:

* `event_concepts` — cosine similarity of the event to short descriptions of what moves a business (key rate,
  FX, sanctions and payments, metals, construction and mortgage, grain, fertilizers, fuel, logistics, consumer
  demand, taxes, macro) and of what does not (incidents and strikes, culture and sport, foreign politics, the
  US/EU economy). Robust to the tagger: it never sees the tags.
* `profile_text` — the company as a short Russian description built from `company_context` features only
  (no invented facts); `sim_profile` = cosine(event, profile).
* `rerank` — optional multilingual cross-encoder score of (company description, event). Off by default: on
  leave-one-set-out validation it did not add quality on top of the embedding features (it hurt one set) and it
  costs ~0.4 s per pair on 2 vCPU (reports/ranker2.md). Enable with ML_RERANKER_MODEL for experiments.

Models run locally on CPU through fastembed/ONNX (no LLM budget):
  embeddings  sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d; the same model as the RAG)
  reranker    ML_RERANKER_MODEL (off by default; evaluated: jinaai/jina-reranker-v2-base-multilingual)
If fastembed or the weights are unavailable the layer reports `available() == False` and the ranker falls back
to ml-ranker@1 (rules only) — the service keeps working.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alfa_ml.log import get_logger
from alfa_ml.profile import fv, products_of

log = get_logger(__name__)

EMBED_MODEL = os.environ.get("ML_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
RERANK_MODEL = os.environ.get("ML_RERANKER_MODEL", "")
EVAL_RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"  # the cross-encoder of the ablation
CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH") or os.environ.get("ML_MODELS_DIR")

# What moves a business, in plain words; the `need` says which companies it concerns (see `company_needs`).
CONCEPTS: dict[str, str] = {
    "rate": "Решение Банка России по ключевой ставке, денежно-кредитная политика, стоимость кредитов для бизнеса",
    "fx": "Курс рубля к доллару, юаню и евро, валютный рынок, стоимость импорта и экспортная выручка",
    "sanctions": "Санкции против российских компаний и банков, ограничения платежей и расчётов по внешней торговле",
    "metals": "Цены на сталь, металлопрокат и арматуру, металлургия, рынок металлов",
    "construction": "Строительство и девелопмент, ипотека, продажи жилья, спрос на стройматериалы",
    "agro": "Урожай, зерно и пшеница, экспорт зерна, закупочные интервенции, сельское хозяйство",
    "fertilizers": "Удобрения и химическая промышленность, цены на удобрения",
    "fuel": "Цены на дизельное топливо и бензин, топливный рынок России",
    "logistics": "Грузоперевозки и логистика, железная дорога, порты, ставки и тарифы перевозки",
    "consumer": "Потребительский спрос, розничные продажи, продукты питания, цены для населения",
    "tax": "Налоги, пошлины, сборы и новое регулирование для бизнеса в России",
    "macro": "Экономика России: ВВП, инфляция, прогнозы правительства и ЦБ, промышленное производство",
    "neg_incident": "Атака беспилотников, обстрел, авария, пожар, происшествие, погибшие и пострадавшие",
    "neg_culture": "Кино, сериалы, культура, спорт, шоу-бизнес, знаменитости",
    "neg_politics": "Политика и дипломатия, выборы, протесты, заявления политиков других стран",
    "neg_foreign": "Компании и экономика США и Европы, американский фондовый рынок и корпоративные новости",
}
POSITIVE = [k for k in CONCEPTS if not k.startswith("neg_")]
NEGATIVE = [k for k in CONCEPTS if k.startswith("neg_")]

TAG_RU = {
    "metals": "металлы", "metallurgy": "металлургия", "steel": "сталь", "construction": "строительство",
    "developer": "девелопмент", "real_estate": "недвижимость", "construction_materials": "стройматериалы",
    "logistics": "логистика", "trucking": "автоперевозки", "warehousing": "склады", "shipping": "морские перевозки",
    "rail": "железная дорога", "agriculture": "сельское хозяйство", "grain_trade": "торговля зерном", "food": "продукты питания",
    "beverages": "напитки", "retail": "розничная торговля", "consumer_goods": "потребительские товары",
    "oil_gas": "нефть и газ", "fuel": "топливо", "automotive": "автомобили", "automotive_parts": "автозапчасти",
    "electronics": "электроника", "it": "ИТ", "chemicals": "химия", "machinery": "машины и оборудование",
    "energy": "энергетика", "hospitality": "гостиницы и общепит", "wholesale": "оптовая торговля", "finance": "финансы",
    "leasing": "лизинг", "pharma": "фармацевтика", "telecom": "связь", "trade": "торговля", "services": "услуги",
    "engineering": "инжиниринг", "professional_services": "профессиональные услуги", "manufacturing": "производство",
    "food_service": "общепит", "rental": "аренда", "transport": "транспорт", "metals_trade": "торговля металлами",
}  # fmt: skip
COMMODITY_RU = {
    "steel": "сталь", "nonferrous": "цветные металлы", "iron_ore": "железная руда", "coal": "уголь", "oil": "нефть",
    "gas": "газ", "fuel": "топливо", "grain": "зерно", "fertilizers": "удобрения", "gold": "золото",
    "cement": "цемент", "timber": "лес", "sugar": "сахар", "fish": "рыба", "food": "продукты",
}  # fmt: skip
PRODUCT_RU = {
    "loan": "кредит", "leasing": "лизинг", "factoring": "факторинг", "deposit": "депозиты", "guarantee": "гарантии",
    "rko": "расчётный счёт", "payroll": "зарплатный проект",
}  # fmt: skip


def _ru(tags: Sequence[str], table: dict[str, str]) -> str:
    return ", ".join(dict.fromkeys(table.get(t, t) for t in tags))


def profile_text(features: dict[str, Any]) -> str:
    """The company in a few Russian sentences, only from its features (company_context)."""
    parts: list[str] = []
    name = fv(features, "okved.name")
    tags = [t for t in (fv(features, "industry_tags", []) or []) if isinstance(t, str)]
    parts.append(
        f"Компания: {name or 'вид деятельности не указан'}"
        + (f" ({_ru(tags, TAG_RU)})" if tags else "")
        + "."
    )
    region = fv(features, "region")
    if region:
        parts.append(f"Регион: {region}.")
    prods = [PRODUCT_RU.get(p, p) for p in products_of(features) if p in PRODUCT_RU]
    if prods:
        parts.append("В банке: " + ", ".join(prods) + ".")
    fts = [str(c).title() for c in (fv(features, "fts_countries", []) or [])]
    if fts:
        parts.append("Внешняя торговля: " + ", ".join(fts) + ".")
    comm = [str(c) for c in (fv(features, "sensitivities.commodity_exposure", []) or [])]
    if comm:
        parts.append("Сырьё и товары: " + _ru(comm, COMMODITY_RU) + ".")
    for d, who in (("in", "Покупатели"), ("out", "Поставщики")):
        shares = []
        for path in features:
            if path.startswith(f"counterparties.industry_share_{d}."):
                v = fv(features, path)
                tag = path.rsplit(".", 1)[-1]
                if isinstance(v, int | float) and v >= 0.1 and tag not in {"trade", "wholesale", "services"}:
                    shares.append((float(v), TAG_RU.get(tag, tag)))
        if shares:
            top = sorted(shares, reverse=True)[:3]
            parts.append(f"{who}: " + ", ".join(f"{label} {v * 100:.0f}%" for v, label in top) + ".")
    return " ".join(parts)


def company_needs(features: dict[str, Any]) -> dict[str, float]:
    """How much each concept concerns this company (0..1), from its features — the interaction weights."""
    prods = set(products_of(features))
    tags = {t for t in (fv(features, "industry_tags", []) or []) if isinstance(t, str)}
    comm = {str(c) for c in (fv(features, "sensitivities.commodity_exposure", []) or [])}
    fts = bool(fv(features, "fts_countries", []) or [])

    def cp(*names: str) -> float:
        best = 0.0
        for path in features:
            if path.startswith("counterparties.industry_share_") and path.rsplit(".", 1)[-1] in names:
                v = fv(features, path)
                if isinstance(v, int | float):
                    best = max(best, min(1.0, float(v) / 0.25))
        return best

    debt = bool(prods & {"loan", "leasing", "factoring"})
    return {
        "rate": 1.0 if debt else (0.5 if "deposit" in prods else 0.15),
        "fx": 1.0 if fts else 0.1,
        "sanctions": 1.0 if fts else 0.15,
        "metals": max(1.0 if (tags & {"metals", "metallurgy", "steel"} or comm & {"steel", "nonferrous"}) else 0.0, 0.6 * cp("metals", "metallurgy", "steel")),
        "construction": max(1.0 if tags & {"construction", "developer", "real_estate", "construction_materials"} else 0.0, 0.6 * cp("construction", "developer", "real_estate")),
        "agro": max(1.0 if tags & {"agriculture", "grain_trade"} or "grain" in comm else 0.0, 0.5 * cp("agriculture", "grain_trade")),
        "fertilizers": 1.0 if tags & {"chemicals"} or "fertilizers" in comm or tags & {"agriculture"} else 0.0,
        "fuel": max(1.0 if tags & {"logistics", "trucking", "transport"} or comm & {"fuel", "oil"} else 0.0, 0.5 * cp("fuel", "oil_gas")),
        "logistics": max(1.0 if tags & {"logistics", "trucking", "warehousing", "shipping", "rail", "transport"} else 0.0, 0.5 * cp("logistics", "trucking", "transport")),
        "consumer": max(1.0 if tags & {"retail", "food", "consumer_goods", "beverages", "hospitality"} else 0.0, 0.5 * cp("retail", "food", "consumer_goods")),
        "tax": 0.5,
        "macro": 0.3,
    }  # fmt: skip


def event_text(event: dict[str, Any], max_chars: int = 600) -> str:
    title = str(event.get("title") or "")
    summary = str(event.get("summary") or "")
    text = (
        title if summary.startswith(title[:40]) and len(summary) <= len(title) + 5 else f"{title}. {summary}"
    )
    return text[:max_chars]


def rerank_query(features: dict[str, Any]) -> str:
    return profile_text(features) + " Какие новости влияют на выручку, затраты, долг и расчёты этой компании?"


def _key(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


class _LRU(OrderedDict[str, Any]):
    def __init__(self, size: int) -> None:
        super().__init__()
        self.size = size

    def get_or(self, key: str) -> Any:
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key: str, value: Any) -> None:
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)


class Backend:
    """Embeddings + reranker. Implementations: fastembed (production) or a precomputed store (offline eval)."""

    name = "none"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        raise NotImplementedError


class FastembedBackend(Backend):
    name = "fastembed"

    def __init__(self) -> None:
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        threads = int(os.environ.get("ML_EMBED_THREADS", "2"))
        self._emb = TextEmbedding(EMBED_MODEL, cache_dir=CACHE_DIR, threads=threads)
        self._rr = (
            TextCrossEncoder(RERANK_MODEL, cache_dir=CACHE_DIR, threads=threads) if RERANK_MODEL else None
        )
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [list(map(float, v)) for v in self._emb.embed(texts)]

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if self._rr is None:
            return [0.0 for _ in docs]
        with self._lock:
            return [float(s) for s in self._rr.rerank(query, docs)]


class StoreBackend(Backend):
    """Precomputed vectors/scores keyed by text hash (eval/semantic_store.py builds it with fastembed)."""

    name = "store"

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.vectors: dict[str, list[float]] = data["vectors"]
        self.scores: dict[str, float] = data["rerank"]
        self.missing: list[tuple[str, ...]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = self.vectors.get(_key(EMBED_MODEL, t))
            if v is None:
                self.missing.append(("embed", t))
                v = []
            out.append(v)
        return out

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        out = []
        for d in docs:
            s = self.scores.get(_key(RERANK_MODEL, query, d))
            if s is None:
                self.missing.append(("rerank", query, d))
                s = 0.0
            out.append(s)
        return out


_BACKEND: Backend | None = None
_TRIED = False
_EMB_CACHE = _LRU(20_000)
_RR_CACHE = _LRU(50_000)


def set_backend(backend: Backend | None) -> None:
    global _BACKEND, _TRIED
    _BACKEND, _TRIED = backend, True
    _EMB_CACHE.clear()
    _RR_CACHE.clear()


def backend() -> Backend | None:
    global _BACKEND, _TRIED
    if not _TRIED:
        _TRIED = True
        if os.environ.get("ML_SEMANTIC", "1") != "0":
            try:
                _BACKEND = FastembedBackend()
                log.info("semantic_ready", embed=EMBED_MODEL, rerank=RERANK_MODEL)
            except Exception as exc:  # no fastembed or no weights: rules only
                log.warning("semantic_unavailable", error=str(exc)[:300])
                _BACKEND = None
    return _BACKEND


def available() -> bool:
    return backend() is not None


def _norm(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str]) -> list[list[float]]:
    b = backend()
    if b is None:
        return [[] for _ in texts]
    todo = [t for t in dict.fromkeys(texts) if _EMB_CACHE.get_or(_key(EMBED_MODEL, t)) is None]
    if todo:
        for t, v in zip(todo, b.embed(todo), strict=True):
            _EMB_CACHE.put(_key(EMBED_MODEL, t), _norm(v) if v else [])
    return [_EMB_CACHE.get_or(_key(EMBED_MODEL, t)) or [] for t in texts]


def cos(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=False)))


def rerank_score(query: str, doc: str) -> float:
    b = backend()
    if b is None:
        return 0.0
    k = _key(RERANK_MODEL, query, doc)
    cached = _RR_CACHE.get_or(k)
    if cached is None:
        cached = b.rerank(query, [doc])[0]
        _RR_CACHE.put(k, cached)
    return float(cached)


def event_concepts(text: str) -> dict[str, float]:
    vecs = embed([text, *CONCEPTS.values()])
    ev, rest = vecs[0], vecs[1:]
    return {k: cos(ev, v) for k, v in zip(CONCEPTS, rest, strict=True)}


def semantic_features(features: dict[str, Any], event: dict[str, Any]) -> dict[str, float]:
    """Semantic features of a (company, event) pair; empty when the layer is unavailable."""
    if backend() is None:
        return {}
    text = event_text(event)
    prof = profile_text(features)
    concepts = event_concepts(text)
    needs = company_needs(features)
    ev_vec, prof_vec = embed([text, prof])
    pos = [concepts[k] for k in POSITIVE]
    neg = [concepts[k] for k in NEGATIVE]
    out: dict[str, float] = {
        "sem_profile": cos(ev_vec, prof_vec),
        "sem_best_pos": max(pos),
        "sem_best_neg": max(neg),
        "sem_margin": max(pos) - max(neg),
        "sem_need_match": max(concepts[k] * needs[k] for k in POSITIVE),
    }
    if RERANK_MODEL:
        out["sem_rerank"] = rerank_score(rerank_query(features), text)
    for k in POSITIVE:
        out[f"c_{k}"] = concepts[k]
        out[f"cn_{k}"] = concepts[k] * needs[k]
    for k in NEGATIVE:
        out[f"c_{k}"] = concepts[k]
    return out


def top_concept(features: dict[str, Any], event: dict[str, Any]) -> tuple[str, float] | None:
    """The concept that ties the event to the company best (for the explanation in the trace)."""
    if backend() is None:
        return None
    concepts = event_concepts(event_text(event))
    needs = company_needs(features)
    k = max(POSITIVE, key=lambda c: concepts[c] * needs[c])
    return k, concepts[k]
