"""Offline evaluation of the ML service: ranking (vs backend baseline@1) and insight quality.

Data: demo companies and fixture news of the backend synthetic set (the same the demo stack shows), company
contexts built from the CSVs by the backend's own build_context (eval/contexts.py), demo bank analytics PDFs.
Gold labels: eval/labels.json (0/1/2 per company x event).

    cd ml && uv run python -m eval.run_eval                       # rules@1 analyst (no LLM)
    cd ml && uv run python -m eval.run_eval --llm-base-url http://localhost:8000/internal/llm --llm-token <token>

Writes reports/eval.md and eval/out/*.json.
"""

import argparse
import asyncio
import json
import math
import re
import statistics
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from app.domain.insight_validator import (
    ValidationContext,
    validate_insight,
)
from app.domain.scoring import EventProfile, profile_from_context, score_candidate
from app.domain.tagging import tag_news
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from alfa_ml import llm as llm_mod
from alfa_ml import tools_client
from alfa_ml.agent.checks import check_insight
from alfa_ml.agent.runner import Agent
from alfa_ml.agent.textutil import stems
from eval.contexts import ROOT, CsvCompanies

SYN = ROOT / "backend" / "data" / "synthetic"
HERE = Path(__file__).resolve().parent
NS = uuid.UUID("6f1c2b8e-0f4a-4c55-9a0e-5d3c1b2a9e10")


def load_events(now: datetime) -> dict[str, dict[str, Any]]:
    items = json.loads((SYN / "news" / "fixture_news.json").read_text(encoding="utf-8"))["items"]
    clusters: dict[str, list[dict[str, Any]]] = {}
    for n in items:
        clusters.setdefault(n.get("cluster") or n["id"], []).append(n)
    events = {}
    for key, news in clusters.items():
        text_title, text_body = news[0]["title"], " ".join(n["body"] for n in news)
        t = tag_news(text_title, text_body)
        first = now - timedelta(hours=max(n["age_hours"] for n in news))
        events[key] = {
            "event_id": str(uuid.uuid5(NS, key)),
            "title": text_title,
            "summary": news[0]["body"],
            "event_type": t.event_type,
            "direction": t.direction,
            "urgent": t.event_type in {"monetary_policy", "sanctions"},
            "urgency_reasons": [],
            "first_seen_at": first.isoformat(),
            "tags": {
                "industry_tags": t.industry_tags,
                "countries": t.countries,
                "commodities": t.commodities,
                "regions": t.regions,
                "entities": [],
                "currencies": t.currencies,
                "numbers": t.numbers,
            },
            "news": [
                {
                    "news_id": str(uuid.uuid5(NS, n["id"])),
                    "title": n["title"],
                    "url": f"https://example.org/{n['id']}",
                    "publisher": "Демо-лента",
                    "published_at": (now - timedelta(hours=n["age_hours"])).isoformat(),
                    "body": {"trust": "untrusted", "content": n["body"], "truncated": False, "flags": []},
                }
                for n in news
            ],
        }
    return events


def load_analytics() -> list[dict[str, Any]]:
    chunks = []
    for pdf in sorted((SYN / "analytics").glob("*.pdf")):
        text = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True
        ).stdout
        title = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("ДЕМО")), pdf.stem)
        for page_no, page in enumerate(text.split("\f"), start=1):
            for para in re.split(r"\n\s*\n", page):
                body = " ".join(para.split())
                if len(body) < 80 or body.startswith("Синтетический демонстрационный"):
                    continue
                cid = str(uuid.uuid5(NS, f"{pdf.name}:{page_no}:{body[:40]}"))
                chunks.append(
                    {
                        "chunk_id": cid,
                        "document_id": str(uuid.uuid5(NS, pdf.name)),
                        "doc_title": title,
                        "doc_date": None,
                        "page": page_no,
                        "section": None,
                        "text": body,
                        "extraction": "text_layer",
                        "ocr_confidence": None,
                        "score": 0.0,
                    }
                )
    return chunks


def search(chunks: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    q = stems(query)
    scored = [(len(q & stems(c["text"] + " " + c["doc_title"])) / (1 + len(q)), c) for c in chunks]
    scored = [x for x in scored if x[0] >= 0.15]
    scored.sort(key=lambda x: -x[0])
    return [{**c, "score": round(s, 4)} for s, c in scored[:limit]]


def make_tools(
    cc: CsvCompanies,
    events: dict[str, dict[str, Any]],
    chunks: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> FastAPI:
    app = FastAPI()
    by_id = {e["event_id"]: e for e in events.values()}

    @app.post("/internal/tools/{tool}")
    async def tool(tool: str, request: Request) -> JSONResponse:
        b = await request.json()
        if tool == "log_step":
            return JSONResponse({"step_id": str(uuid.uuid4())}, status_code=201)
        if tool == "get_company_context":
            return JSONResponse(cc.context(b["company_id"]))
        if tool == "get_event":
            return JSONResponse(by_id[b["event_id"]])
        if tool == "search_bank_analytics":
            return JSONResponse({"hits": search(chunks, b["query"], int(b.get("limit", 6)))})
        if tool == "search_news":
            return JSONResponse({"hits": []})
        if tool == "get_counterparties":
            return JSONResponse(cc.counterparties(b["company_id"], int(b.get("limit", 10))))
        if tool == "get_group":
            return JSONResponse(cc.group(b["company_id"], int(b.get("members_limit", 10))))
        if tool == "get_bank_products":
            return JSONResponse({"products": products})
        return JSONResponse({"title": "unknown"}, status_code=404)

    return app


def dcg(rels: list[int]) -> float:
    return sum((2**r - 1) / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at(ranked: list[str], gold: dict[str, int], k: int) -> float:
    ideal = dcg(sorted(gold.values(), reverse=True)[:k])
    return dcg([gold[e] for e in ranked[:k]]) / ideal if ideal else 1.0


def precision_at(ranked: list[str], gold: dict[str, int], k: int) -> float:
    return sum(1 for e in ranked[:k] if gold[e] > 0) / k


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", default="cfo,ceo")
    ap.add_argument("--llm-base-url", default=None)
    ap.add_argument("--llm-token", default=None)
    args = ap.parse_args()
    now = datetime.now(UTC)
    labels = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
    companies = list(labels["companies"])
    cc = CsvCompanies(SYN)
    events = load_events(now)
    chunks = load_analytics()
    products = [
        {
            "product_id": p["id"],
            "name": p["name"],
            "description": p.get("description", ""),
            "tags": p["tags"],
            "url": p.get("url"),
            "source": "catalog",
        }
        for p in json.loads(
            (ROOT / "backend" / "data" / "reference" / "bank_products.json").read_text(encoding="utf-8")
        )["products"]
    ]
    transport = httpx.ASGITransport(app=make_tools(cc, events, chunks, products))
    tools_client.TRANSPORT = transport
    if not args.llm_base_url:
        llm_mod.TRANSPORT = transport
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)

    rows: list[dict[str, Any]] = []
    for role in args.roles.split(","):
        for cid in companies:
            ctx = cc.context(cid)
            prof = profile_from_context(cid, ctx["features"])
            for key, ev in events.items():
                gold = labels["labels"][key][cid]
                tags = ev["tags"]
                base = score_candidate(
                    prof,
                    EventProfile(
                        event_id=ev["event_id"],
                        event_type=ev["event_type"],
                        direction=ev["direction"],
                        industry_tags=frozenset(tags["industry_tags"]),
                        countries=frozenset(tags["countries"]),
                        commodities=frozenset(tags["commodities"]),
                        regions=frozenset(tags["regions"]),
                        currencies=frozenset(tags["currencies"]),
                        company_ids=frozenset(),
                        urgent=ev["urgent"],
                        first_seen_at=datetime.fromisoformat(ev["first_seen_at"]),
                    ),
                    role,
                    now,
                )
                req = {
                    "request_id": f"eval_{cid}_{key}_{role}",
                    "company_id": cid,
                    "event_id": ev["event_id"],
                    "role": role,
                    "deadline_ms": 60000,
                    "schema_version": "1.0.0",
                    "tools": {
                        "base_url": "http://eval/internal/tools",
                        "llm_base_url": args.llm_base_url or "http://eval/internal/llm",
                        "token": args.llm_token or "eval",
                        "run_id": f"run_eval_{cid}_{key}_{role}",
                    },
                    "options": {"allow_llm": bool(args.llm_base_url)},
                    "candidate": {
                        "score": base.score,
                        "breakdown": base.breakdown,
                        "matched_features": base.matched_features,
                    },
                }
                started = time.perf_counter()
                res = await Agent(req).run()
                latency = int((time.perf_counter() - started) * 1000)
                row: dict[str, Any] = {
                    "role": role,
                    "company_id": cid,
                    "event": key,
                    "title": ev["title"],
                    "gold": gold,
                    "baseline": base.score,
                    "status": res.status,
                    "ml": 0.0,
                    "latency_ms": latency,
                    "llm_calls": res.usage.get("llm_calls", 0),
                }
                if res.insight:
                    ins = res.insight
                    row["ml"] = ins["relevance"]["score"]
                    val = validate_insight(
                        ins,
                        ValidationContext(
                            company_id=cid,
                            event_id=ev["event_id"],
                            feature_paths=frozenset(ctx["features"]),
                            known_products=frozenset(p["product_id"] for p in products),
                            chunk_exists=lambda c: any(ch["chunk_id"] == c for ch in chunks),
                        ),
                    )
                    from alfa_ml.agent.evidence import Evidence
                    from alfa_ml.profile import compute_exposure

                    evd = Evidence(
                        company_id=cid,
                        role=role,
                        context=ctx,
                        event=ev,
                        exposure=compute_exposure(ctx["features"], ev, cid),
                    )
                    evd.add_news(ev["news"])
                    q = check_insight(ins, evd)
                    row.update(
                        valid=val.ok,
                        problems=val.reasons(),
                        impact=ins["impact"]["level"],
                        polarity=ins["polarity"],
                        n_statements=len(ins["statements"]),
                        grounded_share=q.metrics.get("grounded_share"),
                        retelling=q.metrics.get("retelling_overlap"),
                        personal_features=len({f for r in ins["why_it_matters"] for f in r["feature_refs"]}),
                        bank=ins["bank_view"]["coverage"],
                        quant=len(ins["impact"]["quantitative"]),
                        hypotheses=sum(1 for s in ins["statements"] if s["kind"] == "hypothesis"),
                        insight_title=ins["title"],
                        quality_issues=[i["code"] for i in q.quality],
                    )
                    (out_dir / f"{cid}_{key}_{role}.json").write_text(
                        json.dumps(ins, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
                rows.append(row)
    (out_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    report = render(rows, bool(args.llm_base_url))
    (HERE.parent / "reports" / "eval.md").write_text(report, encoding="utf-8")
    print(report)


def render(rows: list[dict[str, Any]], llm: bool) -> str:
    lines = [
        "# Оценка ML-сервиса (офлайн)",
        "",
        f"Сгенерировано `ml/eval/run_eval.py` · аналитик: {'LLM через Gateway' if llm else 'rules@1 (без LLM)'} · {datetime.now(UTC):%Y-%m-%d}",
        "",
    ]
    lines += [
        "Данные: 5 демо-компаний и 15 событий демо-ленты бэкенда (синтетика, повторяет особенности реальных данных); профили собраны тем же `build_context`, что и в Tools API; разметка — `ml/eval/labels.json` (0 — нерелевантно, 1 — фон, 2 — важно).",
        "",
    ]
    lines += [
        "## Ранжирование ленты",
        "",
        "| Роль | Скорер | nDCG@5 | P@3 | P@5 | Recall важных (gold=2) в показанных | Доля нерелевантных (gold=0) в показанных |",
        "|---|---|---|---|---|---|---|",
    ]
    for role in sorted({r["role"] for r in rows}):
        rr = [r for r in rows if r["role"] == role]
        for name, key in (("baseline@1 (бэкенд)", "baseline"), ("ml-ranker@1 + skip агента", "ml")):
            nd, p3, p5, rec, noise = [], [], [], [], []
            for cid in sorted({r["company_id"] for r in rr}):
                cr = [r for r in rr if r["company_id"] == cid]
                gold = {r["event"]: r["gold"] for r in cr}
                ranked = [r["event"] for r in sorted(cr, key=lambda r: -r[key])]
                nd.append(ndcg_at(ranked, gold, 5))
                p3.append(precision_at(ranked, gold, 3))
                p5.append(precision_at(ranked, gold, 5))
                shown = [r for r in cr if r[key] > 0]
                important = [r for r in cr if r["gold"] == 2]
                rec.append(sum(1 for r in important if r[key] > 0) / max(1, len(important)))
                noise.append(sum(1 for r in shown if r["gold"] == 0) / max(1, len(shown)))
            lines.append(
                f"| {role} | {name} | {statistics.mean(nd):.3f} | {statistics.mean(p3):.3f} | {statistics.mean(p5):.3f} | {statistics.mean(rec):.3f} | {statistics.mean(noise):.3f} |"
            )
    done = [r for r in rows if r["status"] == "completed"]
    lines += ["", "## Качество инсайтов", ""]
    if done:
        lines += [
            "| Метрика | Значение |",
            "|---|---|",
            f"| Сгенерировано / пропущено (`skipped`) / ошибок | {len(done)} / {sum(r['status'] == 'skipped' for r in rows)} / {sum(r['status'] == 'failed' for r in rows)} |",
            f"| Прошли валидатор бэкенда | {sum(r['valid'] for r in done) / len(done):.1%} |",
            f"| Доля утверждений со ссылкой на источник (groundedness) | {statistics.mean(r['grounded_share'] for r in done):.1%} |",
            f"| Признаков компании в «почему важно» (среднее) | {statistics.mean(r['personal_features'] for r in done):.2f} |",
            f"| Пересказ: пересечение заголовка/сути с текстом новости (среднее, ниже — лучше) | {statistics.mean(r['retelling'] for r in done):.2f} |",
            f"| Инсайтов с позицией банка | {sum(r['bank'] != 'none' for r in done) / len(done):.1%} |",
            f"| Инсайтов с количественной оценкой | {sum(r['quant'] > 0 for r in done) / len(done):.1%} |",
            f"| Инсайтов с явно помеченными гипотезами | {sum(r['hypotheses'] > 0 for r in done) / len(done):.1%} |",
            f"| Задержка генерации, медиана / p95, мс | {statistics.median(r['latency_ms'] for r in done):.0f} / {sorted(r['latency_ms'] for r in done)[int(0.95 * (len(done) - 1))]:.0f} |",
            f"| Вызовов LLM на инсайт (среднее) | {statistics.mean(r['llm_calls'] for r in done):.2f} |",
        ]
    fp = [r for r in rows if r["status"] == "completed" and r["gold"] == 0]
    fn = [r for r in rows if r["status"] != "completed" and r["gold"] == 2]
    lines += [
        "",
        "## Ошибки отбора (для разбора)",
        "",
        f"Показаны нерелевантные (gold=0): {len(fp)}; пропущены важные (gold=2): {len(fn)}.",
        "",
    ]
    for r in fp[:10]:
        lines.append(f"- лишнее: {r['role']} · {r['company_id']} · «{r['title'][:70]}» (ml={r['ml']:.2f})")
    for r in fn[:10]:
        lines.append(
            f"- пропуск: {r['role']} · {r['company_id']} · «{r['title'][:70]}» (baseline={r['baseline']:.2f})"
        )
    lines += ["", "## Примеры заголовков (роль CFO)", ""]
    for r in [x for x in done if x["role"] == "cfo"][:12]:
        lines.append(f"- {r['company_id']} · {r['insight_title']} — влияние {r['impact']}, {r['polarity']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
