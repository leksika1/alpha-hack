"""Walk the jury scenario over HTTP against a running stack and print what the user would see.

    docker compose exec api python scripts/demo_check.py            # inside the stack
    cd backend && uv run python scripts/demo_check.py --url http://localhost:8000

Exit code 0 when every step passed. Uses only the public API and the admin API (demo accounts from README).
"""

import argparse
import json
import sys
import time
import uuid
from typing import Any

import httpx

OK, FAIL = "[ok]", "[!!]"


class Check:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.failed = 0

    def step(self, title: str, ok: bool, detail: str = "") -> bool:
        print(f"{OK if ok else FAIL} {title}" + (f"\n     {detail}" if detail else ""))
        if not ok:
            self.failed += 1
        return ok


def login(client: httpx.Client, user: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"login": user, "password": password})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def sse(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [x for x in block.splitlines() if x and not x.startswith(":")]
        event = next((x[6:].strip() for x in lines if x.startswith("event:")), None)
        data = "\n".join(x[5:].strip() for x in lines if x.startswith("data:"))
        if event:
            out.append((event, json.loads(data) if data else None))
    return out


def wait_feed(client: httpx.Client, h: dict[str, str], company: str, timeout_s: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    while True:
        items: list[dict[str, Any]] = client.get(
            "/api/v1/feed", headers=h, params={"company_id": company}
        ).json()["items"]
        if items or time.monotonic() > deadline:
            return items
        time.sleep(3)


def run(url: str, wait_s: int) -> int:
    client = httpx.Client(base_url=url, timeout=120)
    c = Check(client)
    print(f"== Альфа-Будущее: сценарий проверки ({url})\n")

    ready = client.get("/ready")
    c.step("1. Сервис готов: Postgres, Redis, S3", ready.status_code == 200, ready.text[:200])

    h = login(client, "demo", "demo12345")
    me = client.get("/api/v1/me", headers=h).json()
    companies = me["companies"]
    c.step(
        "2. Вход демо-пользователя; компании и роли",
        bool(companies),
        "; ".join(f"{x['company_id']} — {x['title']} ({x['role']})" for x in companies),
    )
    company = companies[0]["company_id"]

    summary = client.get("/api/v1/feed/summary", headers=h).json()
    c.step(
        "3. Виджет на главной «Аналитика для вашей компании»",
        summary["unread"] > 0,
        f"непрочитанных {summary['unread']}, срочных {summary['urgent']}",
    )

    items = wait_feed(client, h, company, wait_s)
    if not c.step(f"4. Персональная лента {company}", bool(items), f"{len(items)} инсайтов"):
        return 1
    for card in items[:3]:
        print(f"     • {card['title']}  [{card['impact_level']}, релевантность {card['relevance_score']}]")
        print(f"       почему выше: {', '.join(card['priority_reasons']) or '—'}")
    card = items[0]

    full = client.get(
        f"/api/v1/insights/{card['insight_id']}", headers=h, params={"via": "feed", "position": 1}
    )
    insight = full.json()["insight"]
    kinds = sorted({s["kind"] for s in insight["statements"]})
    c.step(
        "5. Карточка: что произошло → почему важно вам → последствия → что делать → позиция банка",
        bool(insight["why_it_matters"] and insight["actions"]),
        f"почему важно: {insight['why_it_matters'][0]['text'][:160]}…\n     утверждения: {', '.join(kinds)}; "
        f"позиция банка: {insight['bank_view'].get('coverage')}",
    )

    trace = client.get(f"/api/v1/insights/{card['insight_id']}/trace", headers=h).json()
    c.step(
        "6. «На чём основан вывод»: признаки компании, источники, шаги агента",
        bool(trace["company_features"] and trace["steps"]),
        f"признаков {len(trace['company_features'])}, источников {len(trace['sources'])}, "
        f"шагов агента {len(trace['steps'])}, вызовов инструментов {len(trace['tool_calls'])}",
    )

    src_types = {s["type"]: s for s in insight["sources"]}
    for kind in ("news", "bank_analytics"):
        if kind in src_types:
            link = client.post(
                f"/api/v1/insights/{card['insight_id']}/source-click",
                headers=h,
                json={"source_id": src_types[kind]["id"]},
            ).json()
            c.step(f"7. Переход к первоисточнику: {kind}", bool(link["url"]), (link["url"] or "")[:120])

    answer = client.post(
        f"/api/v1/insights/{card['insight_id']}/ask",
        headers=h,
        json={"question": "Как это повлияет на наши платежи по кредитам?", "intent": "clarify"},
    )
    events = sse(answer.text)
    done = events[-1][1] if events and events[-1][0] == "done" else None
    c.step(
        "8. Вопрос к аналитике (SSE): ответ с источниками",
        done is not None,
        (done["answer"][:200] + f"…  [источников: {len(done['sources'])}]") if done else str(events[-1:]),
    )

    cta = client.post(f"/api/v1/insights/{card['insight_id']}/actions", headers=h, json={"type": "callback"})
    c.step("9. Действие: обратный звонок → мок-CRM", cta.status_code == 201, cta.json().get("message", ""))

    fb = client.post(
        f"/api/v1/insights/{card['insight_id']}/feedback", headers=h, json={"useful": True}
    ).json()
    c.step(
        "10. Отзыв «полезно» и что сервис с ним сделал",
        bool(fb.get("applied")),
        " ".join(fb.get("applied", [])),
    )

    digest = client.get("/api/v1/digest", headers=h, params={"company_id": company, "period": "week"}).json()
    c.step(
        "11. Дайджест недели",
        digest["counts"]["new"] > 0,
        f"новых {digest['counts']['new']}, рисков {digest['counts']['risks']}, "
        f"возможностей {digest['counts']['opportunities']}",
    )

    ah = login(client, "admin", "admin12345")
    before = client.get("/api/v1/notifications", headers=h).json()
    last_id = max((n["id"] for n in before["items"]), default=0)
    tag = uuid.uuid4().hex[:6]
    news = client.post(
        "/api/v1/admin/news",
        headers=ah,
        json={
            "items": [
                {
                    "title": f"Новые санкции: ограничены экспорт стали и платежи через банки Китая ({tag})",
                    "body": "ЕС и США расширили санкции: под ограничения попали поставки стального проката "
                    "и арматуры, а банки Китая ужесточили проверку платежей российских импортёров.",
                }
            ]
        },
    ).json()
    c.step(
        "12. Новая срочная новость → конвейер (сырьё, дедуп, событие, матчинг, генерация)",
        bool(news.get("events")),
        f"событий {len(news.get('events', []))}, компаний в очереди {len(news.get('companies_queued', []))}",
    )
    deadline = time.monotonic() + wait_s
    delivered: list[Any] = []
    while time.monotonic() < deadline and not delivered:
        stream = client.get(
            "/api/v1/notifications/stream", headers={**h, "Last-Event-ID": str(last_id)}, params={"max_s": 3}
        )
        delivered = [d for e, d in sse(stream.text) if e == "notification" and d["id"] > last_id]
    c.step(
        "13. Уведомление пришло по SSE",
        bool(delivered),
        delivered[0]["title"] if delivered else "не дождались уведомления",
    )

    m = client.get("/api/v1/admin/metrics", headers=ah, params={"days": 1}).json()
    c.step(
        "14. Метрики качества (админ): north star и этапы CJM",
        m["sessions"] > 0,
        f"сессий {m['sessions']}, north star {m['north_star']['value']}, "
        f"полезных {m['quality']['useful_share']}, отклонено валидатором {m['generation']['rejected']}",
    )
    print(f"\n== Итог: {'всё прошло' if not c.failed else f'проблем: {c.failed}'}")
    return 0 if not c.failed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--wait", type=int, default=180, help="seconds to wait for queued generation")
    args = parser.parse_args()
    sys.exit(run(args.url, args.wait))


if __name__ == "__main__":
    main()
