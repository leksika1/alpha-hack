import math
from datetime import date
from pathlib import Path

import pytest

from app.domain.chunking import (
    MAX_CHARS,
    chunk_pages,
    detect_date,
    detect_title,
    detect_topics,
    document_meta,
    is_noise,
    paragraphs,
    trim_junk,
)
from app.integrations.embeddings import FakeEmbedding, to_pgvector
from app.integrations.ocr import tsv_to_text
from app.integrations.pdf import page_texts

SYNTH = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "analytics"
FOOTER = "Альфа-Банк, 2026 г. Все права защищены. Настоящее письмо не является инвестиционной рекомендацией."

OCR_LIKE_PAGE = f"""Центр макроэкономического анализа

11 сентября 2026 г.

Ключевая ставка ЦБ РФ
Ставка без изменения на 14,0%, риторика стала жестче

ЦБ РФ принял решение оставить ставку неизменной на уровне 14,0%: ЦБ РФ на сегодняшнем заседании
принял решение взять паузу. Формально сигнал пресс-релиза остался нейтральным.

Мы ожидаем сохранения паузы на заседании 23 октября: С учетом риторики ЦБ РФ мы меняем наш взгляд на
динамику ключевой ставки на этот год. Теперь мы ожидаем ее сохранения на текущем значении 14,0% до кон-
ца года.

{FOOTER}"""
PAGE_2 = f"""Инфляционные ожидания остаются высокими: глава ЦБ РФ уделила внимание повышенному
уровню ожиданий, инфляция в сентябре замедлилась незначительно.

{FOOTER}"""


def test_paragraphs_join_wrapped_lines_and_hyphenation() -> None:
    paras = paragraphs(OCR_LIKE_PAGE)
    assert any(p.endswith("до конца года.") for p in paras), paras
    assert "Ставка без изменения на 14,0%, риторика стала жестче" in paras


def test_paragraphs_without_blank_lines_split_on_sentence_ends() -> None:
    text = (
        "Заголовок раздела\r\nПервое предложение длинного абзаца, которое переносится\r\n"
        "на вторую строку.\r\nВторой абзац начинается здесь и тоже длинный.\r\n"
    )
    assert paragraphs(text) == [
        "Заголовок раздела",
        "Первое предложение длинного абзаца, которое переносится на вторую строку.",
        "Второй абзац начинается здесь и тоже длинный.",
    ]


def test_chunks_have_sections_pages_and_no_boilerplate() -> None:
    chunks = chunk_pages([(1, OCR_LIKE_PAGE), (2, PAGE_2)])
    assert all("Все права защищены" not in c.text for c in chunks)
    sections = {c.section for c in chunks}
    assert "ЦБ РФ принял решение оставить ставку неизменной на уровне 14,0%" in sections
    assert "Мы ожидаем сохранения паузы на заседании 23 октября" in sections
    rate = next(c for c in chunks if c.section and c.section.startswith("Мы ожидаем"))
    assert rate.page == 1 and "14,0% до конца года" in rate.text
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert {c.page for c in chunks} == {1, 2}


def test_long_paragraphs_are_split_and_capped() -> None:
    sentence = "Это предложение про инфляцию и ставку, оно повторяется для объёма. "
    chunks = chunk_pages([(1, sentence * 80)])
    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHARS + 300 for c in chunks)


def test_metadata_detection() -> None:
    meta = document_meta([(1, OCR_LIKE_PAGE), (2, PAGE_2)], "ALFA-Macro_Insights-Policy_Rate_11Sep.pdf")
    assert meta.doc_date == date(2026, 9, 11)
    assert meta.title.startswith("Ключевая ставка ЦБ РФ")
    assert {"key_rate", "inflation"} <= set(meta.topics)


@pytest.mark.parametrize(
    ("first_page", "file_name", "expected"),
    [
        ("", "Бонд_Бриф_14092026.pdf", date(2026, 9, 14)),
        ("Дата: 22.07.2026", "x.pdf", date(2026, 7, 22)),
        ("", "Inflation_tracker_02.pdf", None),
        ("31 февраля 2026 г.", "x.pdf", None),
        ("ОФЗ 26254 (погашение 13.10.2040 г.)", "Бонд_Бриф_14092026.pdf", date(2026, 9, 14)),
        ("", "Steels_Preview_15Apr2021.pdf", date(2021, 4, 15)),
        ("Выпуск 13.10.2040", "x.pdf", None),
    ],
)
def test_detect_date_fallbacks(first_page: str, file_name: str, expected: date | None) -> None:
    assert detect_date(first_page, file_name) == expected


def test_title_falls_back_to_file_name() -> None:
    assert detect_title("", "Gold_digest_August26.pdf") == "Gold digest August26"
    cover = "+\nРЫНКА ЗОЛОТА\n-\nQO\nO\n(9"
    assert detect_title(cover, "Gold_digest_August26.pdf") == "Gold digest August26"
    assert detect_topics("золото золото") == ["gold"]


def test_title_block_skips_issuer_and_date_and_joins_wrapped_lines() -> None:
    head = "ЦЕНТР МАКРОЭКОНОМИЧЕСКОГО АНАЛИЗА\n31 июля 2026 г.\nМакростатистика 1П26\n"
    head += "Слабый рост смещает наш прогноз роста ВВП на\n0,1% за год\n" + "Текст " * 30
    expected = "Макростатистика 1П26: Слабый рост смещает наш прогноз роста ВВП на 0,1% за год"
    assert detect_title(head, "x.pdf") == expected
    brief = (
        "Рынок облигаций\nБонд Бриф: Итоги недели\nЗаседание ЦБ по ставке добавило долговому рынку негатива"
    )
    assert detect_title(brief, "x.pdf") == "Рынок облигаций — Бонд Бриф: Итоги недели"


def test_topics_need_density_in_long_documents() -> None:
    text = ("Облигации и ОФЗ: доходность кривой. " * 200) + "Курс рубля к доллару."
    assert detect_topics(text) == ["bonds"]
    assert "metals" not in detect_topics("Риторика ЦБ стали жестче, ставки стали выше, " * 3)


@pytest.mark.parametrize(
    ("para", "noise"),
    [
        ("31.01.2024 9.03.2024 06.05.2024 23.06.2024 0.08.2024 27.09.2024 4.11.2024 01.01.2025", True),
        ("0,70 15 2020, 0,60 5 2018 © О 2012 0,50 2 О 2015 10 2013 „- 0.30 Ё 2019 © 0,20 2 5", True),
        ("[== [== [== [== сч ite] a = 5 сей wo [== & = wi [=] fas! г [=] = инфляция", True),
        ("Severstal 212 17 760 1,9x 2,3х 2,2х NLMK 33,1 19 838 1,6x Russia weighted average 1,5x", False),
        ("Рост оборота розничной торговли был на уровне 5,3% г/г в июле.", False),
    ],
)
def test_chart_ocr_noise_is_detected(para: str, noise: bool) -> None:
    assert is_noise(para) is noise


def test_legal_text_and_captions_are_not_indexed() -> None:
    page = (
        "Прогноз ставки\n\nНаш прогноз по ставке на конец 2027 г. остается на уровне 12,0%. "
        "Альфа-Банк, 2026 г. Все права защищены. Генеральная лицензия ЦБ РФ № 1326.\n\n"
        "Источники: Росстат, Альфа-Банк\n\nКонтакты и дисклеймер\n\n"
        "Ценные бумаги с номиналом в иностранной валюте подвержены колебаниям курса.\n"
    )
    chunks = chunk_pages([(1, page)])
    assert [c.section for c in chunks] == ["Прогноз ставки"]
    assert chunks[0].text == "Наш прогноз по ставке на конец 2027 г. остается на уровне 12,0%."


def test_tesseract_tsv_parsing() -> None:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96\tКлючевая",
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t90\tставка",
        "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t80\t14,0%",
        "5\t1\t2\t1\t1\t1\t0\t0\t1\t1\t-1\t",
        "5\t1\t2\t1\t1\t2\t0\t0\t1\t1\t70\tИнфляция",
        "4\t1\t2\t1\t1\t0\t0\t0\t1\t1\t-1\t",
    ]
    text, conf = tsv_to_text("\n".join([header, *rows]))
    assert text == "Ключевая ставка\n14,0%\n\nИнфляция"
    assert conf == pytest.approx((0.96 + 0.90 + 0.80 + 0.70) / 4, abs=1e-3)


async def test_fake_embedding_is_deterministic_normalized_and_lexical() -> None:
    emb = FakeEmbedding()
    a, b, c = await emb.embed_documents(
        ["ключевая ставка ЦБ", "Ключевую ставку сохранили", "цены на зерно выросли"]
    )
    assert len(a) == 384 and math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)
    assert a == await emb.embed_query("ключевая ставка ЦБ")
    cos = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert cos(a, b) > cos(a, c)
    assert to_pgvector([0.5, -1.0]) == "[0.500000,-1.000000]"


def test_synthetic_pdfs_are_labelled_and_have_a_text_layer() -> None:
    files = sorted(SYNTH.glob("*.pdf"))
    assert len(files) == 2
    for f in files:
        pages = page_texts(f.read_bytes())
        assert pages and all(len(p.text) > 200 for p in pages)
        assert all("Синтетический демонстрационный материал" in p.text for p in pages)
        assert "ДЕМО (синтетика)" in pages[0].text


def test_trim_junk_keeps_text_after_chart_debris() -> None:
    glued = (
        "130 40| 2,5% 110 д 2,0 | и 2,0% 70 Источники: Росстат, HeadHunter, Альфа-Банк "
        "Ускорение инфляции будет"
    )
    assert trim_junk(glued) == "Ускорение инфляции будет"
    assert trim_junk("Мы полагаем, что спрос на пике.") == "Мы полагаем, что спрос на пике."
    assert trim_junk("1N25 г. составил 14%, план") == "1N25 г. составил 14%, план"
