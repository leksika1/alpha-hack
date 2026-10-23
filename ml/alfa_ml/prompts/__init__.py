import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent

ROLE_HINTS = {
    "cfo": "финансовый директор — стоимость долга, ликвидность, валютные риски, бюджет, ковенанты.",
    "treasurer": "казначей — ликвидность, курс и валютные позиции, размещение средств, расчёты.",
    "chief_accountant": "главный бухгалтер — налоги, регуляторные требования, отчётность, сроки платежей.",
    "ceo": "генеральный директор — спрос, конкуренты, контрагенты, операционные риски, стратегия.",
    "owner": "собственник — стоимость бизнеса, стратегия группы, крупные риски и возможности.",
    "other": "руководитель компании — ключевые риски и возможности для бизнеса.",
}


@lru_cache
def load(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def version(name: str) -> str:
    m = re.search(r"prompt:\s*([\w@.-]+)", load(name))
    return m.group(1) if m else f"{name}@?"


def system(name: str, role: str | None = None) -> str:
    text = re.sub(r"<!--.*?-->\s*", "", load(name), flags=re.S)
    return text.replace("{role_hint}", ROLE_HINTS.get(role or "other", ROLE_HINTS["other"]))
