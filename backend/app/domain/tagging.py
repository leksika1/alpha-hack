"""Baseline (rule/dictionary) news tagging — ADR-007: the ML team may replace it behind the same output.

Extracts from a news text: industry tags (same vocabulary as data/reference/okved_*.csv), commodities,
countries (spelled as in FTS data), regions (spelled as in REGION), currencies, event type, direction
(up/down/hold), numbers (key rate), urgency reasons and prompt-injection flags. Matching is on lowercase
word stems, so unseen news are handled the same way as fixtures (case criterion 05).
"""

import re
from dataclasses import dataclass, field

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

REGION_STEMS: dict[str, tuple[str, ...]] = {
    "Москва": ("москв", "московск"),
    "Санкт-Петербург": ("санкт-петербург", "петербург"),
    "Свердловская область": ("свердловск", "екатеринбург"),
    "Приморский край": ("приморь", "приморск", "владивосток"),
    "Краснодарский край": ("краснодар", "кубан", "новороссийск"),
    "Новосибирская область": ("новосибирск",),
    "Республика Татарстан": ("татарстан", "казан"),
    "Челябинская область": ("челябинск", "магнитогорск"),
    "Ростовская область": ("ростовск", "ростов-на-дону"),
}

CURRENCY_STEMS: dict[str, tuple[str, ...]] = {
    "USD": ("доллар",),
    "EUR": ("евро ",),
    "CNY": ("юан",),
    "TRY": ("лир",),
    "AED": ("дирхам",),
    "INR": ("рупи",),
}

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

KEY_RATE_RE = re.compile(r"ставк[а-я]*[^.%]{0,60}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
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


_INDUSTRY = _compile(INDUSTRY_STEMS)
_COMMODITY = _compile(COMMODITY_STEMS)
_COUNTRY = _compile(COUNTRY_STEMS)
_REGION = _compile(REGION_STEMS)
_CURRENCY = _compile(CURRENCY_STEMS)
_EVENT_TYPES = tuple((t, tuple(_stem_regex(s) for s in stems)) for t, stems in EVENT_TYPE_RULES)
_URGENT = tuple((r, tuple(_stem_regex(s) for s in stems)) for r, stems in URGENT_RULES)
_UP = tuple(_stem_regex(s) for s in UP_STEMS)
_DOWN = tuple(_stem_regex(s) for s in DOWN_STEMS)
_HOLD = tuple(_stem_regex(s) for s in HOLD_STEMS)
_CBR = tuple(_stem_regex(s) for s in ("банк росси", "цб", "центробанк", "регулятор"))


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
    if event_type == "monetary_policy":
        m = KEY_RATE_RE.search(text)
        if m:
            numbers.append({"kind": "key_rate", "value": float(m.group(1).replace(",", ".")), "unit": "%"})
        if _any(text, _CBR):
            reasons.append("cbr_key_rate_decision")
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
        flags=["suspected_prompt_injection"] if detect_injection(raw) else [],
    )
