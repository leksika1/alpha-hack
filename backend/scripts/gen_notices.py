"""Generate THIRD_PARTY_NOTICES.md from uv.lock + installed package metadata (make notices).

Python packages are listed automatically. Models, datasets, services, docker images and mocked
integrations are maintained by hand in scripts/notices_manual.md and appended verbatim.
"""

import importlib.metadata as md
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/
REPO_ROOT = ROOT.parent


# Packages whose metadata lacks a license field or that are not installable on the dev OS (checked by hand).
LICENSE_OVERRIDES = {
    "colorama": "BSD License",
    "pywin32": "Python Software Foundation License",
    "taskiq-dependencies": "MIT",
    "uvloop": "MIT OR Apache-2.0",
    "win32-setctime": "MIT License",
}


def _license(name: str) -> str:
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]
    try:
        meta = md.metadata(name)
    except md.PackageNotFoundError:
        return "n/a (not installed locally)"
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    if classifiers:
        return ", ".join(sorted(set(classifiers)))
    raw = (meta.get("License") or "").strip().splitlines()
    return raw[0][:80] if raw else "see package"


def main() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
    rows = []
    for pkg in sorted(lock.get("package", []), key=lambda p: p["name"].lower()):
        if pkg["name"] == project:
            continue
        source = pkg.get("source", {})
        origin = "PyPI" if "registry" in source else next(iter(source), "?")
        rows.append(f"| {pkg['name']} | {pkg.get('version', '?')} | {_license(pkg['name'])} | {origin} |")
    manual = (ROOT / "scripts" / "notices_manual.md").read_text(encoding="utf-8")
    out = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
    out.write_text(
        "# THIRD_PARTY_NOTICES\n\n"
        "Файл генерируется `make notices` (scripts/gen_notices.py). "
        "Не редактировать вручную раздел пакетов.\n\n"
        "## Python-пакеты (из uv.lock)\n\n"
        "| Пакет | Версия | Лицензия | Источник |\n|---|---|---|---|\n" + "\n".join(rows) + "\n\n" + manual,
        encoding="utf-8",
    )
    print(f"written {out.relative_to(REPO_ROOT)}: {len(rows)} packages", file=sys.stderr)


if __name__ == "__main__":
    main()
