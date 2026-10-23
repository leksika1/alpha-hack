"""Chunking of extracted PDF pages for RAG (SPEC FR-A3) and document metadata.

Generic for any future bank material (case FAQ #42): no per-file rules.
* paragraphs: blank-line separated blocks; wrapped lines inside a block are joined;
* boilerplate: paragraphs repeated on at least half of the pages (legal footer, header) are dropped;
* sections: a lead sentence ending with ":" ("ЦБ РФ сохранил ставку 14,0%: ...") or a short standalone heading
  line becomes the section of the following text;
* chunks never cross pages (page citations stay exact), ~TARGET chars with a one-sentence overlap.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

TARGET_CHARS = 900
MAX_CHARS = 1500
OVERLAP_MAX = 220
HEADING_MAX = 110
BOILERPLATE_SHARE = 0.5

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
EN_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}

TOPICS: dict[str, tuple[str, ...]] = {
    "key_rate": ("ключевая ставка", "ключевой ставки", "ключевую ставку", "денежно-кредитн", "дкп"),
    "inflation": ("инфляц",),
    "bonds": ("облигац", "офз", "доходност", "кривая", "купон"),
    "gold": ("золот",),
    "metals": ("сталь", "стальн", "металл", "прокат", "руда", "руды"),  # not "стали": a verb too
    "consumption": ("потреблен", "розничн", "потребител"),
    "macro": ("ввп", "промпроизвод", "безработ", "деловой активн", "pmi"),
    "fx": ("курс рубл", "валют", "юан", "доллар"),
    "budget": ("бюджет", "дефицит", "минфин"),
    "credit": ("кредит", "заимствован"),
}
MIN_TOPIC_HITS = 2
TOPIC_HITS_PER_1000_CHARS = 0.3  # long documents mention everything once or twice


@dataclass(frozen=True, slots=True)
class Chunk:
    page: int
    index: int
    section: str | None
    text: str


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    title: str
    doc_date: date | None
    topics: list[str]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


SHORT_LINE = 0.6  # a line shorter than 60% of the median line, followed by a capitalized line, ends a block
END_PUNCT = (".", "!", "?", ":", ";", "»", "…")


def _split_block(block: str) -> list[str]:
    """Text layers often have no blank lines between paragraphs: break after sentence-final lines and after
    short lines (headings, last lines of paragraphs) that are followed by a capitalized line."""
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    if len(lines) < 2:
        return [" ".join(lines)] if lines else []
    median = sorted(len(ln) for ln in lines)[len(lines) // 2]
    parts: list[list[str]] = [[]]
    for i, line in enumerate(lines):
        parts[-1].append(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        starts_upper = bool(nxt) and (nxt[0].isupper() or nxt[0].isdigit())
        if nxt and starts_upper and (line.endswith(END_PUNCT) or len(line) < SHORT_LINE * median):
            parts.append([])
    return [" ".join(p) for p in parts if p]


def paragraphs(page_text: str) -> list[str]:
    text = page_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"-\n(?=[а-яa-z])", "", text)  # hyphenated line breaks: "инфля-\nция"
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        for para in _split_block(block):
            normalized = _norm(para)
            if normalized:
                out.append(normalized)
    return out


_SHORT_WORDS_TEXT = (
    "в на с к и о у а я по за до из от не но же ли бы то мы он их ее её ни со во об ко ну да г гг "
    "т тыс млн млрд трлн руб долл м п пп б of to in on at by vs us eu"
)
SHORT_WORDS = frozenset(_SHORT_WORDS_TEXT.split())  # legitimate 1-2 letter tokens (not OCR debris)
_EDGE = ".,;:!?()[]«»\"'“”„*"
_WORD = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}$")
_NUMERIC = re.compile(r"^[+\-−–]?[\d.,/:]+[%xхXХ]?$|^[A-Za-zА-Яа-яЁё]{1,3}/[A-Za-zА-Яа-яЁё]{1,3}$")
_LETTERS_DIGITS = re.compile(r"^[\wЁё%$€₽/\-+.,:×]+$")
NOISE_JUNK_SHARE = 0.3
MIN_WORDS = 3


def _junk(token: str) -> bool:
    t = token.strip(_EDGE)
    if not t:
        return True
    if _WORD.match(t) or _NUMERIC.match(t) or t.lower() in SHORT_WORDS:
        return False
    if t.isalpha() and t.isupper() and len(t) >= 2:  # abbreviations: ЦБ, РФ, ФРС
        return False
    if not _LETTERS_DIGITS.match(t):
        return True  # "[==", "©", "—N"
    letters = sum(ch.isalpha() for ch in t)
    return letters <= 2 and not any(ch.isdigit() for ch in t)  # stray "wn", "ыы", "oO"


INLINE_SOURCES = re.compile(r"Источники?: (?:[\w\-.]+(?: [\w\-.]+)?(?:, |$| (?=[А-ЯA-Z])))+")


def trim_junk(para: str) -> str:
    """Drops chart debris that OCR glued to a paragraph: a junk prefix (axis labels before the text starts)
    and inline "Источники: Росстат, Альфа-Банк" captions."""
    text = INLINE_SOURCES.sub("", para)
    tokens = text.split()
    for i in range(len(tokens)):
        window = tokens[i : i + 3]
        clean = len(window) == 3 and not any(map(_junk, window))
        alpha = sum(t.strip(_EDGE)[:1].isalpha() for t in window)  # words, abbreviations, prepositions
        if clean and alpha >= 2 and tokens[i].strip(_EDGE)[:1].isalpha():
            debris = any(map(_junk, tokens[:i]))  # "1N25 г. составил" has no debris: keep it whole
            return _norm(" ".join(tokens[i:] if debris else tokens))
    return _norm(text)


def is_noise(para: str) -> bool:
    """OCR of charts and axes: few real words or many junk tokens. Tables with labels are kept."""
    tokens = para.split()
    words = sum(1 for t in tokens if _WORD.match(t.strip(_EDGE)))
    if words < MIN_WORDS:
        return True
    return sum(map(_junk, tokens)) / len(tokens) > NOISE_JUNK_SHARE


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+(?=[А-ЯA-Z0-9«\"(])", text) if s]


def _boilerplate(pages: list[tuple[int, str]]) -> set[str]:
    if len(pages) < 2:
        return set()
    counts = Counter(p for _, text in pages for p in set(paragraphs(text)) if len(p) > 30)
    return {p for p, n in counts.items() if n >= 2 and n / len(pages) >= BOILERPLATE_SHARE}


def _split_lead(para: str) -> tuple[str | None, str]:
    m = re.match(rf"^(.{{10,{HEADING_MAX}}}?):\s+(.+)$", para)
    if m and not re.search(r"[.!?]", m.group(1)) and _is_heading(m.group(1)) and not CAPTION.match(para):
        return m.group(1).strip(), m.group(2).strip()
    return None, para


LEGAL_SENTENCE = re.compile(
    r"все права защищены|генеральная лицензия|не является (?:индивидуальной )?инвестиционн"
    r"|несанкционированное копирование|инвестиционному консультированию|исключительной собственностью"
    r"|all rights reserved|^©?\s*[\w\- ]+, (?:19|20)\d{2} г\.?$",
    re.I,
)
SOURCES_LINE = re.compile(r"^(?:Источники?|Sources?):", re.I)  # "Источники: Росстат, Альфа-Банк"
CAPTION = re.compile(r"^(?:Источник|Source|Рис\.|Рисунок|Табл|Таблица|График|Chart|Figure)", re.I)
LEGAL_SECTION = re.compile(r"дисклеймер|disclaimer|важная информация|правовая информация", re.I)


def _strip_legal(para: str) -> str:
    """Legal footer sentences glued to a content paragraph by OCR are removed, the content is kept."""
    if not LEGAL_SENTENCE.search(para):
        return para
    return " ".join(x for x in _sentences(para) if not LEGAL_SENTENCE.search(x))


def _is_heading(para: str) -> bool:
    if len(para) > HEADING_MAX or re.search(r"[.!?:;]$", para) or CAPTION.match(para):
        return False
    if not (para[0].isupper() or re.match(r"^\d+[.)]\s+[А-ЯA-Z]", para)):  # "в среднем за 5М26" is a label
        return False
    tokens = para.split()
    words = sum(1 for t in tokens if _WORD.match(t.strip(_EDGE)))
    long_word = any(len(t.strip(_EDGE)) >= 5 for t in tokens)  # not an axis: "Апр Май Июнь Июл"
    return words >= 2 and long_word and sum(map(_junk, tokens)) / len(tokens) <= NOISE_JUNK_SHARE


class _PageAccumulator:
    """Collects paragraphs of one page into chunks of ~TARGET_CHARS within the current section."""

    def __init__(self, page: int, out: list[Chunk]) -> None:
        self.page = page
        self.out = out
        self.buf: list[str] = []
        self.section: str | None = None
        self.buf_section: str | None = None

    def flush(self, overlap: bool = True) -> None:
        if not self.buf:
            return
        body = " ".join(self.buf)
        self.out.append(Chunk(self.page, len(self.out), self.buf_section, body))
        last = _sentences(body)[-1] if overlap else ""
        keep = overlap and 0 < len(last) <= OVERLAP_MAX and len(body) > len(last)
        self.buf = [last] if keep else []

    def start_section(self, title: str) -> None:
        self.flush(overlap=False)
        self.section = title
        self.buf_section = title

    def add(self, piece: str) -> None:
        if self.buf and len(" ".join(self.buf)) + len(piece) > TARGET_CHARS:
            self.flush()
            self.buf_section = self.section
        if not self.buf:
            self.buf_section = self.section
        self.buf.append(piece[:MAX_CHARS])


def chunk_pages(pages: list[tuple[int, str]]) -> list[Chunk]:
    noise = _boilerplate(pages)
    chunks: list[Chunk] = []
    legal = False  # inside a "Disclaimer" section (it may continue on the next page)
    for page, text in pages:
        acc = _PageAccumulator(page, chunks)
        for raw in paragraphs(text):
            if raw in noise:
                continue
            if _is_heading(raw):  # before the noise check: headings are short by nature
                acc.start_section(raw)
                legal = bool(LEGAL_SECTION.search(raw))
                continue
            para = trim_junk(_strip_legal(raw))
            if legal or not para or is_noise(para) or (SOURCES_LINE.match(para) and len(para) < 200):
                continue
            lead, body = _split_lead(para)
            if lead:
                acc.start_section(lead)
                para = f"{lead}: {body}"
            for piece in [para] if len(para) <= MAX_CHARS else _sentences(para):
                acc.add(piece)
        acc.flush(overlap=False)
    kept = [c for c in chunks if len(c.text) >= 40]
    return [Chunk(c.page, i, c.section, c.text) for i, c in enumerate(kept)]


DATE_RU = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(20\d{2})", re.I)
DATE_DOT = re.compile(r"\b(\d{2})\.(\d{2})\.(20\d{2})\b")
DATE_FILE = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")
DATE_FILE_EN = re.compile(r"(\d{1,2})(" + "|".join(EN_MONTHS) + r")[a-z]*(\d{4})?", re.I)


MIN_YEAR = 2000
DATE_HEAD_CHARS = (
    600  # the issue date is printed at the top; dates further down are content (bond maturities)
)
ISSUER_HEADERS = ("центр макроэкономического анализа",)  # department captions above the real title
TITLE_SCAN_LINES = 8
TITLE_BODY_LINE = 95  # a longer line is body text
TITLE_MIN_WORDS = 3


def _safe_date(y: int, m: int, d: int) -> date | None:
    if not MIN_YEAR <= y <= date.today().year + 1:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def detect_date(first_page: str, file_name: str) -> date | None:
    """Issue date: Russian date at the top of the first page, else the date in the file name
    (ddmmyyyy / 15Apr2021), else a dd.mm.yyyy date at the top. Unknown -> None (no guessing)."""
    head = first_page[:DATE_HEAD_CHARS]
    candidates: list[date | None] = []
    if m := DATE_RU.search(head):
        candidates.append(_safe_date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))))
    if m := DATE_FILE.search(file_name):
        candidates.append(_safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    if (m := DATE_FILE_EN.search(file_name)) and m.group(3):
        month = EN_MONTHS[m.group(2).lower()[:3]]
        candidates.append(_safe_date(int(m.group(3)), month, int(m.group(1))))
    if m := DATE_DOT.search(head):
        candidates.append(_safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return next((d for d in candidates if d is not None), None)


def _title_line(x: str) -> bool:
    tokens = x.split()
    words = sum(1 for t in tokens if _WORD.match(t.strip(_EDGE)))
    return (
        len(x) >= 8
        and words >= 1
        and words / len(tokens) >= 0.5
        and x[0].isupper()
        and not DATE_RU.search(x)
        and not DATE_DOT.search(x)
        and x.lower() not in ISSUER_HEADERS
    )


def detect_title(first_page: str, file_name: str) -> str:
    """Title block = short lines at the top of the first page before the body text (issuer caption and date
    skipped); a wrapped line is joined. Too short (cover art OCR) -> the file name."""
    lines = [_norm(x).lstrip("_—–-•>[] ") for x in first_page.replace("\r", "\n").split("\n")]
    lines = [x for x in lines if x][:TITLE_SCAN_LINES]
    picked: list[str] = []
    for x in lines:
        if len(x) > TITLE_BODY_LINE:
            break
        continues = x[0].islower() or (x[0].isdigit() and not DATE_RU.search(x))
        wrapped = bool(picked) and continues and not picked[-1].endswith(END_PUNCT)
        if wrapped:
            picked[-1] = f"{picked[-1]} {x}"
        elif _title_line(x):
            if len(picked) == 2 or (picked and ":" in picked[-1]):  # "Бонд Бриф: Итоги недели" ends the block
                break
            picked.append(x)
    title = " — ".join(picked) if any(":" in p for p in picked) else ": ".join(picked)
    if len(title.split()) >= TITLE_MIN_WORDS:
        return title[:200]
    stem = re.sub(r"[_\-]+", " ", file_name.rsplit(".", 1)[0])
    return _norm(stem)[:200]


def detect_topics(full_text: str) -> list[str]:
    text = full_text.lower().replace("ё", "е")
    threshold = max(MIN_TOPIC_HITS, TOPIC_HITS_PER_1000_CHARS * len(text) / 1000)
    return sorted(t for t, keys in TOPICS.items() if sum(text.count(k) for k in keys) >= threshold)


def document_meta(pages: list[tuple[int, str]], file_name: str) -> DocumentMeta:
    first = pages[0][1] if pages else ""
    return DocumentMeta(
        title=detect_title(first, file_name),
        doc_date=detect_date(first, file_name),
        topics=detect_topics(" ".join(t for _, t in pages)),
    )
