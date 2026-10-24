"""Compliance registry shared by the pipelines (ADR-037); the rules live in app.domain.compliance."""

import json
from functools import lru_cache
from pathlib import Path

from app.domain.compliance import RegistryEntry, registry_match

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "compliance_registry.json"


@lru_cache
def registry() -> list[RegistryEntry]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return [RegistryEntry(e["domain"].lower(), e["name"], e["status"]) for e in data["entries"]]


def registry_entry(url: str) -> RegistryEntry | None:
    return registry_match(url, registry())
