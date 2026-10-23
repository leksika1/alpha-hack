"""Robustness on the organisers' real company profiles + demo company candidates.

Builds contexts for active clients from data/raw (the real CSVs, never committed), runs the agent (rules@1) on
the demo news events for a random sample and reports aggregates: how many events reach a company, validity,
personalisation. Also proposes demo companies per archetype (rich, contrasting profiles).

    cd ml && uv run python -m eval.real_profiles --data-dir ../data/raw --sample 60
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from alfa_ml import llm as llm_mod
from alfa_ml import tools_client
from alfa_ml.agent.runner import Agent
from eval.contexts import CsvCompanies
from eval.run_eval import ROOT, load_analytics, load_events, make_tools

HERE = Path(__file__).resolve().parent

ARCHETYPES = {
    "Заёмщик-импортёр (металл/оборудование, Китай)": lambda f: (
        f["loan"] and "КИТАЙ" in f["fts"] and f["commodity"]
    ),
    "Экспортёр сырья (агро/химия)": lambda f: f["fts"] and f["export"] and f["commodity"],
    "Девелопер/стройка с кредитом": lambda f: f["loan"] and {"construction", "developer"} & f["tags"],
    "Ритейл/общепит (потребспрос)": lambda f: f["consumer"] and f["n_known_cp"] >= 5,
    "Логистика с лизингом": lambda f: f["leasing"] and {"logistics", "trucking"} & f["tags"],
}


def signals(cc: CsvCompanies, cid: str) -> dict[str, Any]:
    c = cc.companies[cid]
    o = cc.okved[cid]
    out_df, in_df = cc.out_by.get(cid), cc.in_by.get(cid)
    known = 0
    for df, col in ((out_df, "ben_known"), (in_df, "payer_known")):
        if df is not None:
            known += int(df[col].sum())
    members = cc.groups.get(c.group_id or "", [])
    group_rev = (
        sum(float(cc.companies[m].revenue_rub or 0) for m in members)
        if members
        else float(c.revenue_rub or 0)
    )
    return {
        "loan": "loan" in c.products,
        "leasing": "leasing" in c.products,
        "deposit": "deposit" in c.products,
        "fts": set(c.countries),
        "commodity": bool(o.commodities),
        "export": bool(o.flags.get("export_oriented")),
        "consumer": bool(o.flags.get("consumer_demand")),
        "tags": set(o.industry_tags),
        "n_known_cp": known,
        "group_rev": group_rev,
        "large": group_rev > 15e9,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    t0 = time.time()
    cc = CsvCompanies(Path(args.data_dir))
    clients = [cid for cid, c in cc.companies.items() if c.client_type == "CLIENT"]
    print(
        f"loaded {len(cc.companies)} companies, {len(clients)} clients in {time.time() - t0:.0f}s", flush=True
    )

    # demo candidates: large clients with the richest profiles per archetype
    sig = {cid: signals(cc, cid) for cid in clients}
    demo: dict[str, list[dict[str, Any]]] = {}
    for name, rule in ARCHETYPES.items():
        cands = [cid for cid in clients if sig[cid]["large"] and rule(sig[cid])]
        cands.sort(
            key=lambda cid: (
                -(sig[cid]["n_known_cp"] >= 5) - len(sig[cid]["fts"]) * 0.1 - sig[cid]["loan"],
                -sig[cid]["n_known_cp"],
            )
        )
        demo[name] = [
            {
                "company_id": cid,
                "okved": cc.companies[cid].okved_code,
                "okved_name": cc.companies[cid].okved_name,
                "region": cc.companies[cid].region,
                "products": sorted(cc.companies[cid].products),
                "fts": sorted(sig[cid]["fts"]),
                "known_counterparties": sig[cid]["n_known_cp"],
                "group_revenue_bn": round(sig[cid]["group_rev"] / 1e9, 1),
            }
            for cid in cands[:3]
        ]

    now = datetime.now(UTC)
    events = load_events(now)
    chunks = load_analytics()
    products = [
        {
            "product_id": p["id"],
            "name": p["name"],
            "description": "",
            "tags": p["tags"],
            "url": None,
            "source": "catalog",
        }
        for p in json.loads(
            (ROOT / "backend" / "data" / "reference" / "bank_products.json").read_text(encoding="utf-8")
        )["products"]
    ]
    transport = httpx.ASGITransport(app=make_tools(cc, events, chunks, products))
    tools_client.TRANSPORT = transport
    llm_mod.TRANSPORT = transport
    rnd = random.Random(args.seed)
    sample = rnd.sample(clients, min(args.sample, len(clients)))
    sample += [d["company_id"] for lst in demo.values() for d in lst[:1] if d["company_id"] not in sample]
    stats: Counter[str] = Counter()
    per_company: list[int] = []
    feats: list[int] = []
    by_type: Counter[str] = Counter()
    lat: list[int] = []
    for cid in sample:
        shown = 0
        for key, ev in events.items():
            req = {
                "request_id": f"real_{cid}_{key}",
                "company_id": cid,
                "event_id": ev["event_id"],
                "role": "cfo",
                "deadline_ms": 60000,
                "schema_version": "1.0.0",
                "tools": {
                    "base_url": "http://eval/internal/tools",
                    "llm_base_url": "http://eval/internal/llm",
                    "token": "eval",
                    "run_id": f"run_{cid}_{key}",
                },
                "options": {"allow_llm": False},
            }
            st = time.perf_counter()
            res = await Agent(req).run()
            lat.append(int((time.perf_counter() - st) * 1000))
            stats[res.status] += 1
            if res.insight:
                shown += 1
                by_type[ev["event_type"]] += 1
                feats.append(len({f for r in res.insight["why_it_matters"] for f in r["feature_refs"]}))
        per_company.append(shown)
    total = sum(stats.values())
    lines = [
        "# Проверка на реальных профилях клиентов",
        "",
        f"Сгенерировано `ml/eval/real_profiles.py` на данных организаторов ({len(cc.companies):,} юрлиц, {len(clients):,} действующих клиентов). Только агрегаты; ID компаний — синтетические ID из выдачи организаторов.",
        "",
        f"Выборка: {len(sample)} клиентов (случайные, seed={args.seed}, + кандидаты в демо) × {len(events)} событий демо-ленты = {total} пар; аналитик rules@1.",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Инсайтов / пропусков / ошибок | {stats['completed']} / {stats['skipped']} / {stats['failed']} |",
        f"| Событий в ленте на компанию: медиана / среднее / максимум (из {len(events)}) | {statistics.median(per_company):.0f} / {statistics.mean(per_company):.1f} / {max(per_company)} |",
        f"| Компаний без единого события | {sum(1 for x in per_company if x == 0)} из {len(per_company)} |",
        f"| Признаков компании в «почему важно» (среднее) | {statistics.mean(feats) if feats else 0:.2f} |",
        f"| Задержка генерации, медиана, мс | {statistics.median(lat):.0f} |",
        "",
        "Инсайты по типам событий: " + ", ".join(f"{k} {v}" for k, v in by_type.most_common()) + ".",
        "",
        "## Кандидаты в демо-компании (реальные данные, крупный бизнес)",
        "",
    ]
    for name, lst in demo.items():
        lines.append(f"**{name}**")
        lines.append("")
        for d in lst:
            lines.append(
                f"- `{d['company_id']}` · ОКВЭД {d['okved']} {d['okved_name'][:70] if d['okved_name'] else ''} · {d['region']} · продукты: {', '.join(d['products'])} · ВЭД: {', '.join(d['fts']) or '—'} · известных контрагентов: {d['known_counterparties']} · выручка группы ≈ {d['group_revenue_bn']} млрд ₽"
            )
        lines.append("")
    (HERE.parent / "reports" / "real_profiles.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
