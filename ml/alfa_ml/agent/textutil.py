"""Small text helpers shared by the rule writer, the checks and the chat (no external NLP dependencies)."""

import re

_SENT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z0-9«\"(])")
_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)
_STOP = frozenset(
    [
        "и",
        "в",
        "во",
        "на",
        "по",
        "с",
        "со",
        "к",
        "ко",
        "о",
        "об",
        "от",
        "до",
        "из",
        "за",
        "для",
        "при",
        "над",
        "под",
        "не",
        "ни",
        "что",
        "это",
        "как",
        "а",
        "но",
        "или",
        "же",
        "ли",
        "бы",
        "то",
        "его",
        "ее",
        "её",
        "их",
        "он",
        "она",
        "они",
        "мы",
        "вы",
        "ты",
        "я",
        "год",
        "года",
        "году",
        "млрд",
        "млн",
        "тыс",
        "руб",
        "рублей",
        "также",
        "который",
        "которые",
        "будет",
        "было",
        "были",
        "быть",
        "может",
        "могут",
        "более",
        "менее",
        "после",
        "уже",
        "ещё",
        "еще",
    ]
)


def clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip(" ,.;:—-") + "…"


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) > 2]


def first_sentences(text: str, n: int = 1, max_chars: int = 400) -> str:
    return clip(" ".join(sentences(text)[:n]), max_chars)


def stem(word: str) -> str:
    """Crude Russian stemmer: first 5 letters are enough for overlap scoring of short business texts."""
    w = word.lower()
    return w[:5] if len(w) > 5 else w


def stems(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text or "") if w.lower() not in _STOP and len(w) > 2}


def overlap(a: str, b: str) -> float:
    """Share of stems of `a` present in `b` (0..1)."""
    sa, sb = stems(a), stems(b)
    return len(sa & sb) / len(sa) if sa else 0.0


def best_sentences(text: str, query: str, n: int = 2, min_score: float = 0.0) -> list[str]:
    q = stems(query)
    scored = []
    for i, s in enumerate(sentences(text)):
        if len(s) < 25:
            continue
        st = stems(s)
        score = len(q & st) / (1 + len(q)) + (0.05 if re.search(r"\d", s) else 0.0) - i * 0.002
        scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted((i, s) for score, i, s in scored[:n] if score >= min_score)
    return [s for _, s in chosen]


_RATE_PP = re.compile(
    r"на\s+(\d+(?:[.,]\d+)?)\s*(п\.?\s?п\.?|процентн\w*\s+пункт\w*|б\.?\s?п\.?|базисн\w*\s+пункт\w*)", re.I
)


def rate_change_pp(text: str) -> float | None:
    """Size of a key-rate move mentioned in the text, in percentage points (50 б.п. -> 0.5)."""
    m = _RATE_PP.search(text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit.startswith("б"):
        value /= 100
    return value if 0 < value <= 10 else None


def fmt_pct(share: float) -> str:
    pct = share * 100
    return f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"


def fmt_rub(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.1f} млрд ₽"
    if value >= 1e6:
        return f"{value / 1e6:.0f} млн ₽"
    return f"{value:,.0f} ₽".replace(",", " ")
