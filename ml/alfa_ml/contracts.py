"""Access to the shared contracts (contracts/insight.schema.json) and the constants they imply."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from alfa_ml.config import get_settings

SCHEMA_VERSION = "1.0.0"
SCALE_VERSION = 1
ROLES = ("owner", "ceo", "cfo", "chief_accountant", "treasurer", "other")
EVENT_TYPES = (
    "monetary_policy",
    "fx",
    "commodities",
    "sanctions",
    "regulation_tax",
    "trade_logistics",
    "industry",
    "company",
    "macro_stat",
    "labor",
    "other",
)
HORIZONS = ("days", "weeks", "months", "quarters", "year_plus")
ACTION_TYPES = ("contact_manager", "callback", "application", "analyze", "monitor", "read_source")
STATEMENT_KINDS = ("fact", "bank_analytics", "model_inference", "hypothesis")
POLARITIES = ("risk", "opportunity", "mixed", "neutral")
PARTICIPANT_TYPES = ("company", "government", "regulator", "country", "public_person", "industry", "other")

DISCLAIMER_NOT_ADVICE = "Материал носит информационный характер и не является инвестиционной рекомендацией."
DISCLAIMER_MODEL = (
    "Выводы с пометкой «вывод модели» и «гипотеза» сформированы AI на основе доступных данных "
    "и не являются официальной позицией Альфа-Банка."
)


def contracts_dir() -> Path:
    configured = get_settings().contracts_dir
    if configured:
        return configured
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts" / "insight.schema.json"
        if candidate.exists():
            return candidate.parent
    raise FileNotFoundError("contracts/insight.schema.json not found; set CONTRACTS_DIR")


@lru_cache
def insight_validator() -> Draft202012Validator:
    schema = json.loads((contracts_dir() / "insight.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_errors(payload: dict[str, Any], limit: int = 20) -> list[str]:
    errors = sorted(insight_validator().iter_errors(payload), key=lambda e: list(e.path))
    out = []
    for err in errors[:limit]:
        path = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"{path}: {err.message[:300]}")
    return out
