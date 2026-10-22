"""Select demo companies from the loaded data: python scripts/select_demo_companies.py [--out FILE]

Run after `make ingest-companies` on the organizers' data; the result is used by the seed to give
demo users access to these companies (synthetic data has its own data/synthetic/demo_companies.json).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.pg import pg_connection  # noqa: E402
from app.services.demo_selection import select_demo_companies  # noqa: E402


async def _select() -> list[dict[str, Any]]:
    async with pg_connection() as conn:
        return await select_demo_companies(conn)


def _run(out: Path | None) -> None:
    chosen = asyncio.run(_select())
    text = json.dumps(chosen, ensure_ascii=False, indent=2) + "\n"
    if out:
        out.write_text(text, encoding="utf-8")
        print(f"written {out} ({len(chosen)} companies)")
    else:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    _run(parser.parse_args().out)


if __name__ == "__main__":
    main()
