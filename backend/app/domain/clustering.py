"""Near-duplicate detection and event clustering for news (SPEC FR-N4, FR-N5; ADR-021).

Baseline without embeddings: word-stem overlap of title and lead combined with structured signals from the
tagger (same event type, same direction, overlapping countries / commodities / industries / numbers).
Different reports of one fact share few exact words but the same type and numbers ("ЦБ оставил ставку 14%"
vs "Банк России сохранил ключевую ставку на уровне 14%"), which the structured part captures.
An embedding similarity (phase 4, EmbeddingProvider) can be blended into ``text_similarity`` later.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.domain.tagging import normalize_text

STEM_LEN = 6
STOPWORDS = frozenset(
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
        "не",
        "но",
        "а",
        "же",
        "ли",
        "или",
        "что",
        "как",
        "это",
        "его",
        "ее",
        "их",
        "все",
        "при",
        "про",
        "под",
        "над",
        "без",
        "так",
        "уже",
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
        "может",
        "будет",
        "были",
        "было",
        "была",
    ]
)
NEAR_DUPLICATE = 0.8  # the same article republished / syndicated
SAME_EVENT = 0.5  # different reports of the same fact
MIN_TEXT_OVERLAP = 0.1


@dataclass(frozen=True, slots=True)
class NewsSignature:
    title_stems: frozenset[str]
    lead_stems: frozenset[str]
    event_type: str
    direction: str
    tags: frozenset[str] = field(default_factory=frozenset)


# Light Russian suffix stripping (longest first), then truncation: "банка"/"банк", "ставку"/"ставки".
ENDINGS = tuple(
    sorted(
        [
            "иями",
            "ями",
            "ами",
            "ией",
            "иях",
            "ого",
            "его",
            "ому",
            "ему",
            "ыми",
            "ими",
            "ая",
            "яя",
            "ое",
            "ее",
            "ые",
            "ие",
            "ой",
            "ей",
            "ий",
            "ый",
            "ую",
            "юю",
            "ом",
            "ем",
            "ам",
            "ям",
            "ах",
            "ях",
            "ов",
            "ев",
            "ию",
            "ия",
            "ии",
            "а",
            "я",
            "о",
            "е",
            "ы",
            "и",
            "у",
            "ю",
            "ь",
            "й",
        ],
        key=len,
        reverse=True,
    )
)
MIN_STEM = 4
CONFLICT_FACETS = ("commodity:", "currency:")


def stem(word: str) -> str:
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            word = word[: -len(ending)]
            break
    return word[:STEM_LEN]


def stems(text: str) -> frozenset[str]:
    words = re.findall(r"[а-яa-z0-9]+", normalize_text(text))
    return frozenset(stem(w) for w in words if (len(w) > 2 or w.isdigit()) and w not in STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def signature(
    title: str,
    lead: str,
    event_type: str,
    direction: str,
    tag_values: Iterable[str] = (),
) -> NewsSignature:
    lead_words = " ".join(normalize_text(lead).split()[:60])
    return NewsSignature(
        title_stems=stems(title),
        lead_stems=stems(lead_words),
        event_type=event_type,
        direction=direction,
        tags=frozenset(tag_values),
    )


def tag_values(tags: dict[str, object]) -> list[str]:
    """Flatten tagger output into comparable tokens: country:КИТАЙ, commodity:steel, number:key_rate=14.0."""
    out: list[str] = []
    for key, prefix in (("countries", "country"), ("commodities", "commodity"), ("industry_tags", "industry"),
                        ("regions", "region"), ("currencies", "currency")):  # fmt: skip
        values = tags.get(key) or []
        if isinstance(values, list):
            out.extend(f"{prefix}:{v}" for v in values)
    numbers = tags.get("numbers") or []
    if isinstance(numbers, list):
        out.extend(f"number:{n.get('kind')}={n.get('value')}" for n in numbers if isinstance(n, dict))
    return out


def _subject(tags: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in tags if not t.startswith("number:percent"))


def text_similarity(a: NewsSignature, b: NewsSignature) -> float:
    title = _jaccard(a.title_stems, b.title_stems)
    full = _jaccard(a.title_stems | a.lead_stems, b.title_stems | b.lead_stems)
    return max(title, full)


def event_similarity(a: NewsSignature, b: NewsSignature) -> float:
    """0..1; reports with opposite directions (up vs down) are never the same event."""
    if a.direction in {"up", "down"} and b.direction in {"up", "down"} and a.direction != b.direction:
        return 0.0
    for facet in CONFLICT_FACETS:  # steel vs copper, yuan vs dollar: different subjects
        fa = {t for t in a.tags if t.startswith(facet)}
        fb = {t for t in b.tags if t.startswith(facet)}
        if fa and fb and not fa & fb:
            return 0.0
    same_type = 1.0 if a.event_type == b.event_type and a.event_type != "other" else 0.0
    # generic percentages say nothing about the subject: compare tags without them
    tags = _jaccard(_subject(a.tags), _subject(b.tags))
    # A shared *named* figure (key_rate=14.0) is strong evidence; a generic "8%" is not.
    named = {t for t in a.tags & b.tags if t.startswith("number:") and not t.startswith("number:percent")}
    if named:
        tags = 1.0
    text = text_similarity(a, b)
    score = 0.5 * text + 0.25 * same_type + 0.25 * tags
    if text < MIN_TEXT_OVERLAP:
        score = min(score, SAME_EVENT - 0.01)  # structure alone never merges two reports
    return round(score, 4)


def is_near_duplicate(a: NewsSignature, b: NewsSignature) -> bool:
    return text_similarity(a, b) >= NEAR_DUPLICATE


def best_event(
    candidate: NewsSignature, events: Iterable[tuple[str, NewsSignature]]
) -> tuple[str | None, float]:
    best_id, best = None, 0.0
    for event_id, sig in events:
        s = event_similarity(candidate, sig)
        if s > best:
            best_id, best = event_id, s
    return (best_id, best) if best >= SAME_EVENT else (None, best)
