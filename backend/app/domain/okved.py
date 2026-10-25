"""OKVED-2 classification: code -> section, industry tags, commodities and sensitivity flags.

Reference data lives in data/reference/okved_classes.csv (per 2-digit class) and
okved_overrides.csv (per group/subclass prefix; the longest matching prefix wins for flags,
tags and commodities are accumulated). Adding an industry = editing the CSV, not the code.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_NAMES: dict[str, str] = {
    "A": "Сельское, лесное хозяйство, охота, рыболовство",
    "B": "Добыча полезных ископаемых",
    "C": "Обрабатывающие производства",
    "D": "Электроэнергия, газ и пар",
    "E": "Водоснабжение, отходы",
    "F": "Строительство",
    "G": "Торговля оптовая и розничная",
    "H": "Транспортировка и хранение",
    "I": "Гостиницы и общепит",
    "J": "Информация и связь",
    "K": "Финансы и страхование",
    "L": "Операции с недвижимостью",
    "M": "Профессиональная, научная и техническая деятельность",
    "N": "Административная деятельность",
    "O": "Госуправление",
    "P": "Образование",
    "Q": "Здравоохранение и соцуслуги",
    "R": "Культура, спорт, развлечения",
    "S": "Прочие услуги",
    "T": "Домашние хозяйства",
    "U": "Экстерриториальные организации",
}

FLAG_NAMES = ("consumer_demand", "import_dependent", "export_oriented", "rate_sensitive_industry")

_CODE_RE = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")


@dataclass(frozen=True, slots=True)
class OkvedClass:
    class_code: str
    section: str
    name: str
    industry_tags: tuple[str, ...]
    commodities: tuple[str, ...]
    flags: dict[str, bool]


@dataclass(frozen=True, slots=True)
class OkvedOverride:
    prefix: str
    extra_tags: tuple[str, ...]
    extra_commodities: tuple[str, ...]
    flags: dict[str, bool | None]


@dataclass(frozen=True, slots=True)
class OkvedEntry:
    code: str
    name: str | None
    class_code: str | None
    group_code: str | None
    section: str | None
    section_name: str | None
    industry_tags: list[str]
    commodities: list[str]
    flags: dict[str, bool] = field(default_factory=dict)
    known: bool = True


@dataclass(frozen=True, slots=True)
class OkvedReference:
    classes: dict[str, OkvedClass]
    overrides: list[OkvedOverride]


def _split(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _flag(value: str) -> bool | None:
    value = value.strip()
    if value == "":
        return None
    return value == "1"


def load_reference(directory: Path) -> OkvedReference:
    classes: dict[str, OkvedClass] = {}
    with (directory / "okved_classes.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            classes[row["class_code"]] = OkvedClass(
                class_code=row["class_code"],
                section=row["section"],
                name=row["name"],
                industry_tags=_split(row["industry_tags"]),
                commodities=_split(row["commodities"]),
                flags={f: row[f].strip() == "1" for f in FLAG_NAMES},
            )
    overrides: list[OkvedOverride] = []
    with (directory / "okved_overrides.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            overrides.append(
                OkvedOverride(
                    prefix=row["prefix"],
                    extra_tags=_split(row["extra_tags"]),
                    extra_commodities=_split(row["extra_commodities"]),
                    flags={f: _flag(row[f]) for f in FLAG_NAMES},
                )
            )
    overrides.sort(key=lambda o: len(o.prefix))  # shortest first: longer prefixes override flags
    return OkvedReference(classes=classes, overrides=overrides)


def normalize_code(raw: str | None) -> str | None:
    """'46.90' -> '46.90'; ' 01.11 ' -> '01.11'; '1.11' -> '01.11'; garbage -> None."""
    if raw is None:
        return None
    code = raw.strip()
    if not code:
        return None
    head, _, tail = code.partition(".")
    if head.isdigit() and len(head) == 1:
        code = "0" + head + ("." + tail if tail else "")
    return code if _CODE_RE.match(code) else None


def _matches(code: str, prefix: str) -> bool:
    return code == prefix or (code.startswith(prefix) and len(code) > len(prefix) and prefix.count(".") >= 1)


def classify(code_raw: str | None, name: str | None, ref: OkvedReference) -> OkvedEntry:
    code = normalize_code(code_raw)
    if code is None:
        return OkvedEntry(
            code=(code_raw or "").strip(),
            name=name,
            class_code=None,
            group_code=None,
            section=None,
            section_name=None,
            industry_tags=[],
            commodities=[],
            flags={},
            known=False,
        )
    class_code = code[:2]
    parts = code.split(".")
    group_code = f"{parts[0]}.{parts[1][0]}" if len(parts) > 1 and parts[1] else None
    cls = ref.classes.get(class_code)
    tags: list[str] = list(cls.industry_tags) if cls else []
    commodities: list[str] = list(cls.commodities) if cls else []
    flags: dict[str, bool] = dict(cls.flags) if cls else {f: False for f in FLAG_NAMES}
    for ov in ref.overrides:
        if _matches(code, ov.prefix):
            tags.extend(t for t in ov.extra_tags if t not in tags)
            commodities.extend(c for c in ov.extra_commodities if c not in commodities)
            for name_, val in ov.flags.items():
                if val is not None:
                    flags[name_] = val
    section = cls.section if cls else None
    return OkvedEntry(
        code=code,
        name=name,
        class_code=class_code,
        group_code=group_code,
        section=section,
        section_name=SECTION_NAMES.get(section) if section else None,
        industry_tags=tags,
        commodities=commodities,
        flags=flags,
        known=cls is not None,
    )
