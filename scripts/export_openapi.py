"""Export the public OpenAPI document to docs/openapi.json (make openapi)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    spec = create_app().openapi()
    out = ROOT / "docs" / "openapi.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written {out.relative_to(ROOT)} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
