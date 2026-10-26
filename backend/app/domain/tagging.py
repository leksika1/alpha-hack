"""Baseline (rule/dictionary) news tagging — ADR-007: the ML team may replace it behind the same output.

Extracts from a news text: industry tags (same vocabulary as data/reference/okved_*.csv), commodities,
countries (spelled as in FTS data), regions (official names, app/domain/regions.py), currencies, event type,
direction (up/down/hold), numbers (key rate), urgency reasons and prompt-injection flags. Matching is on
lowercase word stems, so unseen news are handled the same way as fixtures (case criterion 05).

Foreign sources (ADR-039): English stems map to the same keys (industries, commodities, countries as spelled
in FTS data, currencies, event types, direction, urgency), so an English news item matches companies exactly
like a Russian one; `language` is detected from the alphabet. Short English roots that start other words
(port -> portfolio, fed -> fedex) are given as phrases.
"""

import re
from dataclasses import dataclass, field

from app.domain.regions import REGION_STEMS

INDUSTRY_STEMS: dict[str, tuple[str, ...]] = {
    "metals": (
        "металл",
        "металлург",
        "прокат",
        "сталь",
        "стальн",
        "арматур",
        "чугун",
        "цветмет",
        "производств стали",
        "выпуск стали",
        "экспорт стали",
        "импорт стали",
        "тонн стали",
        "цен на стал",
        "стали и прокат",
    ),
    "metallurgy": ("металлург", "меткомбинат", "сталелит"),
    "steel": (
        "сталь",
        "стальн",
        "прокат",
        "арматур",
        "производств стали",
        "выпуск стали",
        "экспорт стали",
        "импорт стали",
        "тонн стали",
        "цен на стал",
        "стали и прокат",
    ),
    "construction": ("строител", "стройк", "застройщ", "девелоп", "жилищн", "ввод жиль", "стройматериал"),
    "developer": ("девелоп", "застройщ"),
    "real_estate": ("недвижим", "ипотек", "аренд", "коммерческ недвиж"),
    "construction_materials": ("цемент", "стройматериал", "кирпич", "бетон"),
    "logistics": ("логист", "перевоз", "грузоперевоз", "фрахт", "контейнер", "транзит", "ржд", "экспедир"),
    "trucking": ("автоперевоз", "грузовик", "автомобильн перевоз", "дальнобой", "тягач"),
    "warehousing": ("склад", "фулфилмент"),
    "shipping": ("морск перевоз", "фрахт", "судоход", "портов", "морск порт"),
    "rail": ("ржд", "железнодорож", "вагон"),
    "agriculture": ("зерн", "пшениц", "урожа", "аграри", "сельхоз", "агропром", "посев", "удобрен", "аграрн"),
    "grain_trade": ("экспорт зерн", "зерновой экспорт", "пошлин на пшениц", "пошлин на зерн", "зернотрейд"),
    "food": ("продовольств", "продукт питан", "пищев", "молок", "мясн", "мяса"),
    "beverages": ("напит", "пиво", "алкогол"),
    "retail": ("ритейл", "розничн", "торговых сет", "магазин", "покупател"),
    "consumer_goods": ("потребительск товар", "бытов техник", "одежд"),
    "oil_gas": ("нефт", "газов", "нефтегаз", "нпз", "бензин", "дизел"),
    "fuel": ("топлив", "бензин", "дизел", "горюч"),
    "automotive": ("автомоб", "автопром", "автодилер", "автокредит"),
    "automotive_parts": ("автозапчаст", "запчаст", "автокомпонент"),
    "electronics": ("электрон", "микросхем", "смартфон", "компьютер", "полупроводник"),
    "it": ("программн обеспеч", "софт", "ит-компан", "it-компан", "цифровиз", "облачн"),
    "chemicals": ("химическ", "химпром", "удобрен", "полимер"),
    "machinery": ("станк", "оборудован", "машиностро", "спецтехник"),
    "energy": ("электроэнерг", "энергосбыт", "тариф на электр"),
    "hospitality": ("гостиниц", "отел", "туризм", "общепит", "ресторан"),
    "wholesale": ("оптов", "дистрибут", "дистрибьют"),
    "finance": ("банковск сектор", "кредитован"),
    "leasing": ("лизинг",),
    "pharma": ("лекарств", "фармацевт"),
    "telecom": ("телеком", "сотов", "связи операт"),
}

# Generic tags too broad to count as an industry match on their own.
GENERIC_TAGS = frozenset({"trade", "manufacturing", "services", "finance"})

COMMODITY_STEMS: dict[str, tuple[str, ...]] = {
    "steel": (
        "сталь",
        "стальн",
        "прокат",
        "арматур",
        "сортов",
        "производств стали",
        "выпуск стали",
        "экспорт стали",
        "импорт стали",
        "тонн стали",
        "цен на стал",
        "стали и прокат",
    ),
    "iron_ore": ("железн руд", "железорудн", "окатыш"),
    "coal": ("уголь", "угля", "угольн", "коксующ"),
    "oil": ("нефть", "нефти", "нефтян", "brent", "urals"),
    "gas": ("природн газ", "газа ", "спг", "газопровод"),
    "fuel": ("топлив", "бензин", "дизел", "горюч"),
    "grain": ("зерн", "пшениц", "ячмен", "кукуруз"),
    "fertilizers": ("удобрен", "азотн", "калийн"),
    "gold": ("золот",),
    "nonferrous": ("медь", "меди ", "алюмини", "никел", "цветн металл", "цинк"),
    "cement": ("цемент",),
    "timber": ("лесоматериал", "древесин", "пиломатериал", "лесопром"),
    "sugar": ("сахар",),
    "fish": ("рыб", "минтай", "лосос"),
}

COUNTRY_STEMS: dict[str, tuple[str, ...]] = {
    "КИТАЙ": ("китай", "китая", "китаю", "кнр", "китайск", "пекин"),
    "ТУРЦИЯ": ("турци", "турецк", "анкар"),
    "ИНДИЯ": ("индия", "индии", "индийск"),
    "ОАЭ": ("оаэ", "эмират", "дубай"),
    "КАЗАХСТАН": ("казахстан",),
    "УЗБЕКИСТАН": ("узбекистан",),
    "БЕЛАРУСЬ": ("беларус", "белорус"),
    "ЕГИПЕТ": ("египет", "египт"),
    "ИРАН": ("иран",),
    "МОНГОЛИЯ": ("монголи",),
    "США": ("сша", "соединенных штат", "американск"),
    "ГЕРМАНИЯ": ("германи", "немецк"),
    "ИТАЛИЯ": ("итали",),
    "ЛИТВА": ("литв",),
    "ПОЛЬША": ("польш",),
    "ГОНКОНГ": ("гонконг",),
    "ЮЖНАЯ КОРЕЯ": ("южн коре", "южная корея", "южной коре", "корейск"),
    "ГРУЗИЯ": ("грузи",),
    "АЗЕРБАЙДЖАН": ("азербайджан",),
    "АРМЕНИЯ": ("армени",),
    "ВЬЕТНАМ": ("вьетнам",),
    "ИЗРАИЛЬ": ("израил",),
    "КЫРГЫЗСТАН": ("киргиз", "кыргыз"),
    "СЕРБИЯ": ("серби",),
}

CURRENCY_STEMS: dict[str, tuple[str, ...]] = {
    "USD": ("доллар",),
    "EUR": ("евро ",),
    "CNY": ("юан",),
    "TRY": ("лир",),
    "AED": ("дирхам",),
    "INR": ("рупи",),
}

# --- English stems for foreign sources (ADR-039) ---
INDUSTRY_STEMS_EN: dict[str, tuple[str, ...]] = {
    "metals": ("metal", "steel", "rebar", "smelter", "steelmaker", "steel mill", "iron and steel"),
    "metallurgy": ("steelmaker", "steel mill", "smelter", "metallurg"),
    "steel": ("steel", "rebar", "hot-rolled", "flat-rolled", "steelmaker"),
    "construction": ("construction", "homebuilder", "housing start", "builder"),
    "developer": ("property developer", "homebuilder", "real estate developer"),
    "real_estate": ("real estate", "property market", "mortgage", "commercial property"),
    "construction_materials": ("cement", "concrete", "brick", "building material"),
    "logistics": ("logistics", "freight", "cargo", "container", "supply chain", "transit"),
    "trucking": ("trucking", "truckload", "haulage", "truck driver", "freight truck"),
    "warehousing": ("warehouse", "fulfillment"),
    "shipping": ("shipping", "vessel", "tanker", "bulk carrier", "seaport", "port of", "ports", "maritime"),
    "rail": ("railway", "railroad", "rail freight", "railcar"),
    "agriculture": ("grain", "wheat", "harvest", "crop", "farm", "fertiliz", "fertilis", "agricultur"),
    "grain_trade": ("grain export", "wheat export", "grain trade", "grain shipment"),
    "food": ("food", "dairy", "meat", "poultry", "pork", "beef"),
    "beverages": ("beverage", "beer", "brewer", "spirits maker"),
    "retail": ("retail", "retailer", "supermarket", "consumer spending", "grocery chain"),
    "consumer_goods": ("consumer goods", "household appliance", "apparel"),
    "oil_gas": ("oil", "crude", "refiner", "refinery", "lng", "petroleum", "gasoline", "diesel"),
    "fuel": ("fuel", "gasoline", "diesel", "jet fuel"),
    "automotive": ("automaker", "carmaker", "car sales", "auto industry", "vehicle sales"),
    "automotive_parts": ("auto parts", "car parts"),
    "electronics": ("electronics", "semiconductor", "microchip", "smartphone", "chipmaker"),
    "it": ("software", "cloud computing", "tech compan", "tech sector"),
    "chemicals": ("chemical", "fertiliz", "fertilis", "polymer", "petrochemical"),
    "machinery": ("machinery", "industrial equipment", "machine tool", "heavy equipment"),
    "energy": ("electricity", "power price", "power grid", "utility compan"),
    "hospitality": ("hotel", "tourism", "restaurant"),
    "wholesale": ("wholesale", "distributor"),
    "finance": ("banking sector", "bank lending"),
    "leasing": ("leasing",),
    "pharma": ("pharmaceutical", "drugmaker"),
    "telecom": ("telecom", "mobile operator"),
}
COMMODITY_STEMS_EN: dict[str, tuple[str, ...]] = {
    "steel": ("steel", "rebar", "hot-rolled", "flat-rolled"),
    "iron_ore": ("iron ore", "pellet"),
    "coal": ("coal", "coking coal", "thermal coal"),
    "oil": ("oil price", "crude", "brent", "wti", "urals", "oil output", "oil export"),
    "gas": ("natural gas", "lng", "gas price", "gas pipeline"),
    "fuel": ("fuel", "gasoline", "diesel"),
    "grain": ("grain", "wheat", "barley", "corn", "maize"),
    "fertilizers": ("fertiliz", "fertilis", "potash", "urea", "ammonia"),
    "gold": ("gold",),
    "nonferrous": ("copper", "aluminium", "aluminum", "nickel", "zinc"),
    "cement": ("cement",),
    "timber": ("timber", "lumber", "wood pulp"),
    "sugar": ("sugar",),
    "fish": ("salmon", "pollock", "seafood", "fishing"),
}
COUNTRY_STEMS_EN: dict[str, tuple[str, ...]] = {
    "КИТАЙ": ("china", "chinese", "beijing"),
    "ТУРЦИЯ": ("turkiye", "turkish", "ankara"),
    "ИНДИЯ": ("india", "indian", "new delhi"),
    "ОАЭ": ("uae", "emirates", "dubai", "abu dhabi"),
    "КАЗАХСТАН": ("kazakhstan",),
    "УЗБЕКИСТАН": ("uzbekistan",),
    "БЕЛАРУСЬ": ("belarus",),
    "ЕГИПЕТ": ("egypt",),
    "ИРАН": ("iran",),
    "МОНГОЛИЯ": ("mongolia",),
    "США": ("united states", "u.s.", "american", "washington"),
    "ГЕРМАНИЯ": ("germany", "german"),
    "ИТАЛИЯ": ("italy", "italian"),
    "ЛИТВА": ("lithuania",),
    "ПОЛЬША": ("poland", "polish"),
    "ГОНКОНГ": ("hong kong",),
    "ЮЖНАЯ КОРЕЯ": ("south korea", "korean", "seoul"),
    "АЗЕРБАЙДЖАН": ("azerbaijan",),
    "АРМЕНИЯ": ("armenia",),
    "ВЬЕТНАМ": ("vietnam",),
    "ИЗРАИЛЬ": ("israel",),
    "КЫРГЫЗСТАН": ("kyrgyz",),
    "СЕРБИЯ": ("serbia",),
}
CURRENCY_STEMS_EN: dict[str, tuple[str, ...]] = {
    "USD": ("dollar",),
    "EUR": ("euro ",),
    "CNY": ("yuan", "renminbi"),
    "TRY": ("lira",),
    "AED": ("dirham",),
    "INR": ("rupee",),
}
EVENT_TYPE_RULES_EN: dict[str, tuple[str, ...]] = {
    "monetary_policy": (
        "key rate",
        "interest rate",
        "rate cut",
        "rate hike",
        "central bank",
        "federal reserve",
        "the fed",
        "ecb",
        "monetary policy",
        "bank of russia",
    ),
    "sanctions": ("sanction", "embargo", "export control", "price cap", "blacklist"),
    "fx": ("exchange rate", "rouble", "ruble", "dollar index", "devaluation", "currency market"),
    "regulation_tax": ("tariff", "import duty", "export duty", "tax", "levy", "regulator", "new rules"),
    "trade_logistics": ("shipping", "freight", "logistics", "customs", "import", "export", "supply chain"),
    "commodities": ("prices of", "price of", "futures", "benchmark price", "prices rose", "prices fell"),
    "macro_stat": ("inflation", "gdp", "pmi", "unemployment", "industrial output", "retail sales"),
    "labor": ("wage", "labor shortage", "labour shortage", "layoff", "jobs report"),
}
UP_STEMS_EN = (
    "rise",
    "rose",
    "rising",
    "raise",
    "raised",
    "hike",
    "increase",
    "surge",
    "jump",
    "soar",
    "climb",
    "gain",
    "higher",
    "tighten",
)
DOWN_STEMS_EN = (
    "cut",
    "fall",
    "fell",
    "drop",
    "decline",
    "plunge",
    "slump",
    "lower",
    "ease",
    "eased",
    "reduce",
    "slash",
)
HOLD_STEMS_EN = ("held rates", "hold rates", "kept rates", "keep rates", "left rates", "unchanged", "on hold")
URGENT_RULES_EN: dict[str, tuple[str, ...]] = {
    "emergency": ("accident", "explosion", "blaze", "strike", "blockade", "collapse", "disaster"),
    "sharp_move": ("sharp", "plunge", "surge", "record high", "record low", "soar", "crash"),
}


def _with_en(ru: dict[str, tuple[str, ...]], en: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    return {key: ru.get(key, ()) + en.get(key, ()) for key in {**ru, **en}}


# Ordered: the first matching type wins.
EVENT_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "monetary_policy",
        ("ключев ставк", "ключевую ставк", "ключевой ставк", "денежно-кредитн", "ставку на уровне"),
    ),
    ("sanctions", ("санкци", "эмбарго", "ограничительн мер", "sdn", "вторичн ограничен")),
    ("fx", ("курс рубл", "курс доллар", "курс юан", "курса рубл", "девальвац", "валютн рын", "курс евро")),
    (
        "regulation_tax",
        ("пошлин", "налог", "ндс", "законопроект", "постановлени", "минфин предлож", "утилизацион"),
    ),
    (
        "trade_logistics",
        ("логист", "таможн", "импорт", "экспорт", "перевоз", "порта", "портов", "порту", "транзит"),
    ),
    ("commodities", ("цен на", "цены на", "котировк", "биржев", "подорожал", "подешевел")),
    ("macro_stat", ("инфляц", "ввп", "росстат", "промпроизводств", "индекс деловой", "pmi")),
    ("labor", ("дефицит кадр", "зарплат", "безработ", "мрот", "рынок труда")),
)

UP_STEMS = (
    "повыс",
    "повышен",
    "роста",
    "рост цен",
    "рост ставк",
    "вырос",
    "выросл",
    "подорож",
    "увелич",
    "ускор",
    "введ",
    "ужесточ",
)
DOWN_STEMS = ("снизи", "снижен", "сниж", "падени", "упал", "подешев", "сократ", "смягч", "отмен")
HOLD_STEMS = ("сохранил", "сохранить", "оставил", "без изменени", "паузу")

URGENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("emergency", ("авари", "пожар", "чрезвычайн", "забастовк", "блокировк", "обрушен")),
    ("sharp_move", ("резк", "обвал", "рекордн", "максимальн за")),
)

INJECTION_PATTERNS = (
    re.compile(r"ignore (all |the )?(previous|prior|above) instructions", re.I),
    re.compile(r"игнорируй(те)? (все |предыдущ)", re.I),
    re.compile(r"system prompt|системн(ый|ого) промпт", re.I),
    re.compile(r"</?(system|assistant|instructions?)>", re.I),
    re.compile(r"(ты|вы) теперь (не )?(ассистент|модель|агент)", re.I),
    re.compile(r"выполни(те)? (команду|инструкцию)|execute the following", re.I),
)

KEY_RATE_RE = re.compile(r"(?:ставк[а-я]*|rate)[^.%]{0,60}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%")


@dataclass(frozen=True, slots=True)
class NewsTags:
    event_type: str
    direction: str
    industry_tags: list[str]
    commodities: list[str]
    countries: list[str]
    regions: list[str]
    currencies: list[str]
    numbers: list[dict[str, object]] = field(default_factory=list)
    urgency_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    language: str = "ru"
    subjects: list[str] = field(default_factory=list)  # one ongoing story, e.g. cbr_key_rate (ADR-040)

    @property
    def urgent(self) -> bool:
        return bool(self.urgency_reasons)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "direction": self.direction,
            "industry_tags": self.industry_tags,
            "commodities": self.commodities,
            "countries": self.countries,
            "regions": self.regions,
            "currencies": self.currencies,
            "numbers": self.numbers,
            "urgency_reasons": self.urgency_reasons,
            "language": self.language,
            "subjects": self.subjects,
        }


def normalize_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[«»\"“”]", " ", text)
    return re.sub(r"\s+", " ", text)


_WORD = r"[а-яa-z0-9-]*"


def _stem_regex(stem: str) -> re.Pattern[str]:
    """'ключев ставк' -> matches 'ключевую ставку'; every word must start at a word boundary."""
    words = stem.strip().split()
    body = (_WORD + r"\s+").join(re.escape(w) for w in words)
    return re.compile(r"(?<![а-яa-z0-9])" + body)


def _compile(table: dict[str, tuple[str, ...]]) -> dict[str, tuple[re.Pattern[str], ...]]:
    return {key: tuple(_stem_regex(s) for s in stems) for key, stems in table.items()}


_INDUSTRY = _compile(_with_en(INDUSTRY_STEMS, INDUSTRY_STEMS_EN))
_COMMODITY = _compile(_with_en(COMMODITY_STEMS, COMMODITY_STEMS_EN))
_COUNTRY = _compile(_with_en(COUNTRY_STEMS, COUNTRY_STEMS_EN))
_REGION = _compile(REGION_STEMS)
_CURRENCY = _compile(_with_en(CURRENCY_STEMS, CURRENCY_STEMS_EN))
_EVENT_TYPES = tuple(
    (t, tuple(_stem_regex(s) for s in stems + EVENT_TYPE_RULES_EN.get(t, ())))
    for t, stems in EVENT_TYPE_RULES
)
_URGENT = tuple(
    (r, tuple(_stem_regex(s) for s in stems + URGENT_RULES_EN.get(r, ()))) for r, stems in URGENT_RULES
)
_UP = tuple(_stem_regex(s) for s in UP_STEMS + UP_STEMS_EN)
_DOWN = tuple(_stem_regex(s) for s in DOWN_STEMS + DOWN_STEMS_EN)
_HOLD = tuple(_stem_regex(s) for s in HOLD_STEMS + HOLD_STEMS_EN)
_CBR = tuple(
    _stem_regex(s) for s in ("банк росси", "цб", "центробанк", "bank of russia", "russian central bank")
)
# Other central banks: their decisions are not the Bank of Russia's, even if the text says "регулятор"
_FOREIGN_CB = tuple(
    _stem_regex(s)
    for s in (
        "фрс", "федеральн резерв", "ецб", "европейск центробанк", "банк англи", "банк япони", "народн банк",
        "federal reserve", "the fed", "ecb", "european central bank", "bank of england", "bank of japan",
    )
)  # fmt: skip
# A rate figure is the decision only when it is not a forecast, an average, a year or a range
_NOT_DECISION = re.compile(r"прогноз|средн|ожидан|консенсус|forecast|average|expect")
_YEAR = re.compile(r"(?<!\d)20\d\d(?!\d)")
_RANGE = re.compile(r"\d\s*[-–—]\s*\d")
# Live blogs ("Военная операция на Украине. Онлайн", "Курс рубля: онлайн-трансляция") are not news of one
# fact: they are skipped at ingestion (ADR-040). "онлайн-ритейл", "продажи онлайн" are not live blogs.
_LIVEBLOG = re.compile(
    r"(?:^|[.:—–|]\s*)онлайн\.?\s*$|онлайн[- ]трансляц|в режиме онлайн|хроника событий"
    r"|главное за (?:день|сутки)|главное к этому часу|\blive updates?\b|\blive:|\bas it happened\b"
    r"|\blive blog\b"
)


def key_rate_decision(text: str) -> float | None:
    """The key rate figure of a decision (normalized text), skipping forecasts, averages, years and ranges."""
    for m in KEY_RATE_RE.finditer(text):
        before = text[max(0, m.start() - 30) : m.start()]
        span = m.group(0)
        if _NOT_DECISION.search(before + span) or _YEAR.search(span) or _RANGE.search(span):
            continue
        return float(m.group(1).replace(",", "."))
    return None


def is_liveblog(title: str) -> bool:
    return bool(_LIVEBLOG.search(normalize_text(title).strip()))


# Press releases with no business consequence: commemorative and investment coins of the Bank of Russia
_NOT_BUSINESS = re.compile(
    r"монет[аы]? банка россии|памятн\w* монет|инвестиционн\w* монет|монет\w* из драгоценн"
    r"|commemorative coins?"
)


def is_not_business(title: str) -> bool:
    return bool(_NOT_BUSINESS.search(normalize_text(title)))


_CYRILLIC = re.compile(r"[а-я]")
_LATIN = re.compile(r"[a-z]")


def detect_language(text: str) -> str:
    """ru or en by the alphabet of the letters (lowercased text)."""
    cyr, lat = len(_CYRILLIC.findall(text)), len(_LATIN.findall(text))
    return "en" if lat > 2 * cyr else "ru"


def _any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def _match(text: str, table: dict[str, tuple[re.Pattern[str], ...]]) -> list[str]:
    return sorted(key for key, patterns in table.items() if _any(text, patterns))


def _direction(text: str) -> str:
    if _any(text, _HOLD):
        return "hold"
    up = sum(len(p.findall(text)) for p in _UP)
    down = sum(len(p.findall(text)) for p in _DOWN)
    if up > down:
        return "up"
    if down > up:
        return "down"
    return "none"


def detect_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def strip_injection(text: str) -> str:
    """Drop sentences that look like instructions to a model; the rest of the news text is kept as data."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if not detect_injection(s)).strip()


def tag_news(title: str, body: str) -> NewsTags:
    raw = f"{title}. {body}"
    text = normalize_text(raw) + " "
    event_type = next((t for t, patterns in _EVENT_TYPES if _any(text, patterns)), None)
    industries = _match(text, _INDUSTRY)
    if event_type is None:
        event_type = "industry" if industries else "other"
    direction = _direction(text)
    numbers: list[dict[str, object]] = []
    reasons: list[str] = []
    subjects: list[str] = []
    if event_type == "monetary_policy":
        rate = key_rate_decision(text)
        if rate is not None:
            numbers.append({"kind": "key_rate", "value": rate, "unit": "%"})
        if _any(text, _CBR) and not _any(text, _FOREIGN_CB):
            reasons.append("cbr_key_rate_decision")
            # every report on the Bank of Russia's rate within the clustering window is one event (ADR-040)
            subjects.append("cbr_key_rate")
    elif event_type == "sanctions":
        reasons.append("sanctions")
    for reason, patterns in _URGENT:
        if _any(text, patterns) and (reason != "sharp_move" or event_type in {"fx", "commodities"}):
            reasons.append(reason)
    if not numbers:
        m = PERCENT_RE.search(text)
        if m:
            numbers.append({"kind": "percent", "value": float(m.group(1).replace(",", ".")), "unit": "%"})
    return NewsTags(
        event_type=event_type,
        direction=direction,
        industry_tags=industries,
        commodities=_match(text, _COMMODITY),
        countries=_match(text, _COUNTRY),
        regions=_match(text, _REGION),
        currencies=_match(text, _CURRENCY),
        numbers=numbers,
        urgency_reasons=sorted(set(reasons)),
        flags=(["suspected_prompt_injection"] if detect_injection(raw) else [])
        + (["liveblog"] if is_liveblog(title) else [])
        + (["not_business"] if is_not_business(title) else []),
        language=detect_language(text),
        subjects=subjects,
    )
