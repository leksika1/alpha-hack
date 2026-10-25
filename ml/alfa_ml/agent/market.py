"""Official Bank of Russia figures for the insight (Tools API get_market_data): key rate and exchange rates.

Why: news quote rates and exchange rates loosely ("курс около 80", an old rate in a digest). The agent asks for the
official series when the event or the company's exposure is about the rate or the currency, cites it as source
`sm` (type external_stat, publisher «Банк России») and checks the figures of the news against it. A mismatch is
not hidden: the card states the official value next to the news figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from alfa_ml.profile import fv

FX_WORDS = {
    "usd_rub": re.compile(r"доллар|\$|usd", re.I),
    "eur_rub": re.compile(r"\bевро\b|eur\b|€", re.I),
    "cny_rub": re.compile(r"юан|cny|renminbi", re.I),
}
COUNTRY_CCY = {"КИТАЙ": "cny_rub", "ГОНКОНГ": "cny_rub", "США": "usd_rub"}
_RATE_CTX = re.compile(r"ставк|дкп", re.I)
_PCT = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:%|процент)", re.I)
_RUB = re.compile(r"(\d{2,3}(?:[.,]\d{1,4})?)\s*(?:руб|₽|рубл)", re.I)
RATE_TOLERANCE = 0.01  # percentage points
FX_TOLERANCE = 0.03  # 3 %: exchange and forecast quotes differ from the official rate a little


def wanted(event: dict[str, Any], exposure_names: set[str], features: dict[str, Any], text: str) -> list[str]:
    """Indicators worth checking for this event and company."""
    etype = str(event.get("event_type") or "other")
    out: list[str] = []
    if etype == "monetary_policy" or "rate" in exposure_names or _RATE_CTX.search(text):
        out.append("key_rate")
    fx_event = etype == "fx" or "fx_country" in exposure_names or "курс" in text.lower()
    if fx_event:
        named = [code for code, rx in FX_WORDS.items() if rx.search(text)]
        if not named:
            fts = fv(features, "fts_countries", []) or []
            named = sorted({COUNTRY_CCY[str(c).upper()] for c in fts if str(c).upper() in COUNTRY_CCY})
        out += named or ["usd_rub"]
    return list(dict.fromkeys(out))[:3]


@dataclass
class Official:
    code: str
    title: str
    unit: str
    value: float
    day: str  # the latest published date of this value
    since: str  # the value is in force since this date
    prev_value: float | None
    prev_day: str | None
    url: str | None
    valid: tuple[float, ...] = ()  # values in force around the publication (a decision takes effect later)

    def phrase(self) -> str:
        v = f"{self.value:g}".replace(".", ",")
        unit = "%" if self.unit == "%" else f" {self.unit}"
        if self.code == "key_rate":
            tail = ""
            if self.prev_value is not None:
                p = f"{self.prev_value:g}".replace(".", ",")
                tail = f", до этого — {p}%"
            return f"{self.title} — {v}% (действует с {ru_date(self.since)}{tail})"
        return f"{self.title} — {v}{unit} на {ru_date(self.day)}"


def ru_date(day: str | None) -> str:
    if not day:
        return "дату публикации"
    try:
        return date.fromisoformat(day[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return day


def official(series: dict[str, Any], on: str | None = None) -> Official | None:
    """The latest official value (what the reader needs today) and the values in force around the publication
    date of the news (to check its figures: a rate decision takes effect a few days after the news)."""
    points = sorted(
        (p for p in series.get("points") or [] if isinstance(p.get("value"), int | float)),
        key=lambda p: p["date"],
    )
    if not points:
        return None
    cut = (on or "")[:10]
    last = points[-1]
    prev = next((p for p in reversed(points) if p["value"] != last["value"]), None)
    since = next((p["date"] for p in points if prev is None or p["date"] > prev["date"]), last["date"])
    src = series.get("source") or {}
    before = [p for p in points if cut and p["date"] <= cut]
    around = [p["value"] for p in points if cut and _days(cut, p["date"]) <= 14] + (
        [before[-1]["value"]] if before else []
    )
    return Official(
        code=str(series.get("indicator")),
        title=str(series.get("title") or series.get("indicator")),
        unit=str(series.get("unit") or ""),
        value=float(last["value"]),
        day=str(last["date"]),
        since=str(since),
        prev_value=float(prev["value"]) if prev else None,
        prev_day=str(prev["date"]) if prev else None,
        url=src.get("url"),
        valid=tuple(float(v) for v in {last["value"], *around}),
    )


def _days(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days)
    except ValueError:
        return 999


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def check(text: str, off: Official) -> dict[str, Any] | None:
    """Figures of the news that disagree with the official value; None when the news quotes nothing comparable."""
    found: list[float] = []
    if off.code == "key_rate":
        for m in _PCT.finditer(text):
            window = text[max(0, m.start() - 60) : m.start()]
            if _RATE_CTX.search(window) and 3 <= _num(m.group(1)) <= 30:
                found.append(_num(m.group(1)))
        bad = [v for v in found if all(abs(v - ok) > RATE_TOLERANCE for ok in off.valid or (off.value,))]
    else:
        rx = FX_WORDS[off.code]
        for m in _RUB.finditer(text):
            window = text[max(0, m.start() - 80) : m.end() + 20]
            if rx.search(window):
                found.append(_num(m.group(1)))
        bad = [v for v in found if all(abs(v - ok) / ok > FX_TOLERANCE for ok in off.valid or (off.value,))]
    if not found:
        return None
    found, bad = list(dict.fromkeys(found)), list(dict.fromkeys(bad))
    return {
        "indicator": off.code,
        "official": off.value,
        "official_date": off.day,
        "news_values": found[:5],
        "mismatch": bool(bad),
        "mismatched_values": bad[:3],
    }


def date_window(published: str | None) -> tuple[str, str]:
    try:
        d = datetime.fromisoformat((published or "").replace("Z", "+00:00")).date()
    except ValueError:
        d = datetime.now().date()
    today = datetime.now().date()
    return date.fromordinal(d.toordinal() - 45).isoformat(), date.fromordinal(
        today.toordinal() + 1
    ).isoformat()
