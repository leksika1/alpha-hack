"""Download the live feed of a running stand as a regression set for ML noise (eval/live, ADR-ML-010).

News texts belong to their publishers and are not committed: they are written to eval/out/live/ (git-ignored).
Labels (eval/live/labels_*.json) are keyed by sha1(title)[:12], so a fresh download of the same period matches them.

    cd ml && uv run python -m eval.live.fetch_live --url https://dev.alpha.leksika1.ru --out eval/out/live/news_live.json
    cd ml && uv run python -m eval.run_eval --news eval/out/live/news_live.json \
        --labels eval/live/labels_live_2026-10-24.json --roles cfo --report reports/live_feed.md

Demo logins are the public demo accounts of the stand (README); override with STAND_LOGINS="login:password,...".
"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_LOGINS = "demo:demo12345,demo2:demo12345"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="eval/out/live/news_live.json")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.url.rstrip("/") + "/api/v1", timeout=60)
    events: dict[str, dict[str, Any]] = {}
    for pair in os.environ.get("STAND_LOGINS", DEFAULT_LOGINS).split(","):
        login, password = pair.split(":", 1)
        tok = c.post("/auth/login", json={"login": login, "password": password}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        for comp in c.get("/companies", headers=h).json():
            cid = comp.get("company_id") or comp.get("id")
            cursor = None
            while True:
                params = {"company_id": cid, "limit": 50, **({"cursor": cursor} if cursor else {})}
                page = c.get("/feed", headers=h, params=params).json()
                for item in page.get("items", []):
                    if item["event_id"] in events:
                        continue
                    ins = c.get(f"/insights/{item['insight_id']}", headers=h).json()["insight"]
                    news = next((s for s in ins.get("sources", []) if s.get("type") == "news"), {})
                    events[item["event_id"]] = {
                        "title": ins["event"]["title"],
                        "body": (ins["event"].get("summary") or "")[:450],
                        "backend_type": ins["event"]["event_type"],
                        "occurred_at": ins["event"].get("occurred_at"),
                        "publisher": news.get("publisher"),
                        "url": news.get("url"),
                    }
                cursor = page.get("next_cursor")
                if not cursor:
                    break
    ref = datetime.now(UTC)
    items = []
    for e in events.values():
        key = hashlib.sha1(e["title"].encode()).hexdigest()[:12]
        at = datetime.fromisoformat(str(e.pop("occurred_at") or ref.isoformat()).replace("Z", "+00:00"))
        items.append(
            {"id": key, "cluster": key, "age_hours": max(0, round((ref - at).total_seconds() / 3600)), **e}
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(items)} events -> {out}")


if __name__ == "__main__":
    main()
