"""Article extraction from a page a user sends (ADR-038)."""

from datetime import UTC, datetime

from app.integrations.news.article import extract_article, looks_like_feed

PAGE = """<html><head><title>Сайт — главная</title>
<meta property="og:title" content="Цены на арматуру выросли на 8%">
<meta property="og:site_name" content="Металл Инфо">
<meta property="article:published_time" content="2026-10-23T09:30:00+03:00">
<script>var tracking = "Этот скрипт не должен попасть в текст статьи никогда";</script>
</head><body>
<nav><p>Главная · Новости · Аналитика · Контакты · Подписка на рассылку новостей сайта</p></nav>
<article>
<p>Металлотрейдеры подняли цены на арматуру на 8% из-за роста спроса со стороны строительных компаний.</p>
<p>Аналитики ожидают, что дефицит проката сохранится до конца квартала, а цены останутся высокими.</p>
</article>
<p>Комментарии читателей к этой статье будут опубликованы после модерации нашей редакцией.</p>
<footer><p>© Металл Инфо, все права защищены, перепечатка только с разрешения редакции</p></footer>
</body></html>"""


def test_extracts_title_text_date_and_publisher() -> None:
    article = extract_article(PAGE.encode())
    assert article.title == "Цены на арматуру выросли на 8%"
    assert article.publisher == "Металл Инфо"
    assert article.published_at == datetime(2026, 10, 23, 6, 30, tzinfo=UTC)
    assert article.text.startswith("Металлотрейдеры подняли цены") and "дефицит проката" in article.text
    for noise in ("скрипт", "Подписка", "Комментарии", "перепечатка"):
        assert noise not in article.text


def test_page_without_article_falls_back_to_paragraphs_and_title() -> None:
    page = "<html><head><title>Новость дня</title></head><body><p>" + "Длинный абзац текста. " * 5 + "</p>"
    article = extract_article(page)
    assert article.title == "Новость дня" and article.text.startswith("Длинный абзац")
    assert article.published_at is None


def test_feeds_are_recognized() -> None:
    assert looks_like_feed(b'<?xml version="1.0"?><rss>', None)
    assert looks_like_feed(b"<html>", "application/rss+xml")
    assert not looks_like_feed(PAGE.encode(), "text/html")
