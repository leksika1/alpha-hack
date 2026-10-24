"""Compliance with Russian law for what the service shows (ADR-037): news it collects or is given, the
insights and answers it publishes. Deterministic rules, a safety net — not a legal review.

Checked per sentence, not across a long text:
* `armed_forces_discredit` — discrediting the Armed Forces of the Russian Federation or their actions
  (art. 20.3.3 of the Administrative Code, art. 207.3 and 280.3 of the Criminal Code): accusations aimed at
  Russia ("Russian aggression", "invasion by Russia"), or a term for Russian forces next to an accusation of
  crimes (war crimes, atrocities, looting) in one sentence;
* `sanctions_call` — calls to impose sanctions against Russia, its citizens or companies (art. 284.2 CrC);
* `extremism_call` — calls to extremist or terrorist activity, justification of terrorism (art. 205.2, 280).

News report what others do (a call for sanctions, a criminal case on extremism), so only discrediting blocks a
news item. The service's own words (insights, answers) must contain none of the three.
Sites of organisations from the official registries (undesirable, foreign agents) are checked by domain:
`data/reference/compliance_registry.json`.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_FRAGMENT = 200

# Terms for the Russian armed forces and their operation
_MILITARY = (
    r"российск\w* арми\w*",
    r"вооруженн\w* сил\w* (?:рф|российск|росси)\w*",
    r"\bвс\s+рф\b",
    r"военнослужащ\w*",
    r"минобороны",
    r"министерств\w* обороны",
    r"\bсво\b",
    r"специальн\w* военн\w* операци\w*",
    r"российск\w* (?:войск|солдат|военн)\w*",
    r"russian (?:army|troops|soldiers|military|forces|armed forces)",
    r"armed forces of russia",
)
# Accusations of crimes: count next to a term for Russian forces in the same sentence
_CRIMES = (
    r"военн\w* преступлен\w*",
    r"зверств\w*",
    r"мародер\w*",
    r"карател\w*",
    r"геноцид\w*",
    r"war crimes?",
    r"atrocit\w+",
    r"looting",
    r"genocide",
)
# Accusations aimed at Russia itself: enough on their own
_RUSSIA_ACCUSED = (
    r"российск\w* (?:агресси|вторжени|оккупаци|оккупант|захватчик)\w*",
    r"(?:агресси|вторжени|нападени)\w* росси\w*",
    r"росси\w* (?:напал|вторглась|оккупировал)\w*",
    r"оккупированн\w* росси\w*",
    r"russian (?:aggression|invasion|occupation|occupiers|war crimes)",
    r"russia'?s (?:war|invasion|aggression|occupation)",
    r"invasion by russia",
)
_AGAINST_RUSSIA = r"[^.!?]{0,40}(?:против|в отношении)\s+(?:росси|рф\b|российск)"
_SANCTIONS_CALL = (
    r"(?:призыва|призыв|призвал|требу)\w*[^.!?]{0,60}санкци\w*" + _AGAINST_RUSSIA,
    r"(?:необходимо|нужно|следует|давайте|должн\w*)\s+(?:ввести|ужесточить|усилить|расширить)[^.!?]{0,30}"
    r"санкци\w*" + _AGAINST_RUSSIA,
    r"(?:call(?:s|ed)? for|urge[sd]?|must|should)[^.!?]{0,60}sanctions?[^.!?]{0,40}(?:against|on)\s+russia",
)
_EXTREMISM_CALL = (
    r"призыв\w*[^.!?]{0,40}(?:к\s+)?(?:свержени|насильственн|массов\w* беспорядк|террор|экстремист)\w*",
    r"оправдани\w* террор\w*",
    r"(?:call(?:s|ed)? for|urge[sd]?)[^.!?]{0,40}(?:overthrow|violent|terror)\w*",
)

_FLAGS = re.IGNORECASE | re.UNICODE


def _compile(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [re.compile(p, _FLAGS) for p in patterns]


_MILITARY_RE = _compile(_MILITARY)
_CRIMES_RE = _compile(_CRIMES)
_RUSSIA_ACCUSED_RE = _compile(_RUSSIA_ACCUSED)
_SANCTIONS_RE = _compile(_SANCTIONS_CALL)
_EXTREMISM_RE = _compile(_EXTREMISM_CALL)
_SENTENCES = re.compile(r"(?<=[.!?…])\s+|\n+")

LAW = {
    "armed_forces_discredit": "дискредитация ВС РФ (ст. 20.3.3 КоАП, ст. 207.3, 280.3 УК РФ)",
    "sanctions_call": "призывы к санкциям против России (ст. 284.2 УК РФ)",
    "extremism_call": "призывы к экстремизму или терроризму (ст. 205.2, 280 УК РФ)",
    "registry_source": "материалы организации из реестра нежелательных организаций или иностранных агентов",
}
NEWS_RULES = frozenset({"armed_forces_discredit"})
OUTPUT_RULES = frozenset({"armed_forces_discredit", "sanctions_call", "extremism_call"})


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    fragment: str

    @property
    def law(self) -> str:
        return LAW[self.code]


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _violates(code: str, sentence: str) -> bool:
    if code == "armed_forces_discredit":
        return _any(_RUSSIA_ACCUSED_RE, sentence) or (
            _any(_MILITARY_RE, sentence) and _any(_CRIMES_RE, sentence)
        )
    if code == "sanctions_call":
        return _any(_SANCTIONS_RE, sentence)
    return _any(_EXTREMISM_RE, sentence)


def check_text(text: str, rules: frozenset[str] = OUTPUT_RULES) -> list[Finding]:
    """One finding per rule: the first sentence that triggers it."""
    findings: dict[str, Finding] = {}
    for sentence in _SENTENCES.split(text.replace("ё", "е").replace("Ё", "Е")):
        sentence = sentence.strip()
        if not sentence:
            continue
        for code in sorted(rules - findings.keys()):
            if _violates(code, sentence):
                findings[code] = Finding(code, sentence[:MAX_FRAGMENT])
    return list(findings.values())


def check_texts(texts: list[str], rules: frozenset[str] = OUTPUT_RULES) -> list[Finding]:
    return check_text("\n".join(t for t in texts if t), rules)


def strings_of(value: object) -> list[str]:
    """Every string inside a JSON-like value (an insight or an answer)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_of(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_of(v)]
    return []


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    domain: str
    name: str
    status: str  # undesirable | foreign_agent | blocked


def registry_match(url: str, registry: list[RegistryEntry]) -> RegistryEntry | None:
    """The registry entry whose domain is the URL's host or its parent domain."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for entry in registry:
        if host == entry.domain or host.endswith("." + entry.domain):
            return entry
    return None
