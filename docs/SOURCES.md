# Источники новостей

Каталог — `backend/data/reference/news_sources.json` (загружается в `core.sources` при каждом сборе).
Только законные бесплатные способы — публичные RSS (FAQ п.35). Проверка — 2026-10-22, с машины разработки:
HTTP-статус, валидная лента (число записей), разрешение `robots.txt` для нашего User-Agent.

## Работающие источники (в каталоге)

| id | Источник | Лента | Темы | Роль |
|---|---|---|---|---|
| `cbr_press` | Банк России — пресс-релизы | `https://www.cbr.ru/rss/RssPress` | регулятор, ДКП | основной |
| `cbr_events` | Банк России — события | `https://www.cbr.ru/rss/eventrss` | регулятор | резерв для `cbr_press` |
| `interfax` | Интерфакс | `https://www.interfax.ru/rss.asp` | общие, бизнес | основной |
| `finmarket` | Финмаркет (группа Интерфакс) | `https://www.finmarket.ru/rss/mainnews.asp` (windows-1251) | рынки | резерв для `interfax` |
| `rbc` | РБК — главные новости | `https://rssexport.rbc.ru/rbcnews/news/30/full.rss` | общие, бизнес | основной |
| `vedomosti_economics` | Ведомости — экономика | `https://www.vedomosti.ru/rss/rubric/economics` | экономика | основной |
| `vedomosti_news` | Ведомости — лента | `https://www.vedomosti.ru/rss/news` | общие | резерв для `vedomosti_economics` |
| `prime` | Прайм | `https://1prime.ru/export/rss2/index.xml` | рынки, экономика | основной |
| `ria` | РИА Новости — лента | `https://ria.ru/export/rss2/archive/index.xml` | общие | резерв для `prime` |
| `gazeta_business` | Газета.ru — бизнес | `https://www.gazeta.ru/export/rss/business.xml` | бизнес | основной |
| `rg` | Российская газета | `https://rg.ru/xml/index.xml` | общие, регулирование | основной |
| `bfm` | BFM.ru | `https://www.bfm.ru/news.rss` | бизнес, рынки | основной |
| `forbes` | Forbes Россия | `https://www.forbes.ru/newrss.xml` | бизнес | основной |
| `retail_ru` | Retail.ru | `https://www.retail.ru/rss/news/` | ритейл | основной |
| `new_retail` | New Retail | `https://new-retail.ru/rss/` | ритейл | резерв для `retail_ru` |
| `zerno` | Zerno.ru | `https://www.zerno.ru/rss.xml` | агро, зерно | основной |
| `fixture` | Демо-лента (синтетика) | `backend/data/synthetic/news/fixture_news.json` | демо | только при `NEWS_FIXTURES_ENABLED=true` |

Живой прогон 2026-10-22: 11 основных лент — 200 OK, 587 новостей за один опрос, ошибок разбора нет; Интерфакс,
Ведомости и Retail.ru поддерживают условные запросы (повторный опрос — 304 Not Modified).

## Нерабочие и исключённые кандидаты

| Кандидат | Адрес | Результат | Причина исключения |
|---|---|---|---|
| Коммерсантъ (экономика, лента) | `kommersant.ru/RSS/...` | 200, лента валидна | **`robots.txt` запрещает** доступ нашему агенту |
| Lenta.ru (экономика) | `lenta.ru/rss/news/economics` | 200 | **`robots.txt` запрещает** |
| Banki.ru | `banki.ru/xml/news.rss` | 200 | **`robots.txt` запрещает** |
| ТАСС | `tass.ru/rss/v2.xml` | 200, но `robots.txt` → 403 | по RFC 9309 4xx допускает доступ, но не берём из осторожности до явного разрешения |
| РИА — экономика | `ria.ru/export/rss2/economy/index.xml` | 404 | заменено общей лентой `ria` (резерв) |
| Минфин России | `minfin.gov.ru/...?rss=Y`, `/press-center/rss/` | HTML / 404 | нет RSS |
| Минэкономразвития | `economy.gov.ru/material/news/rss/`, `/material/rss/` | 404 | нет RSS |
| Металлоснабжение и сбыт | `metalinfo.ru/ru/news/rss.xml`, `/ru/rss/news.xml` | 503 | недоступен |
| Металлторг | `metaltorg.ru/rss/` | 200, HTML | не лента |
| Агроинвестор | `agroinvestor.ru/rss/...` | 404 / HTML | нет RSS |
| Нефтегаз.ру | `neftegaz.ru/rss/` | 468 | защита от ботов |
| Нефть и капитал | `oilcapital.ru/rss` | 200, HTML | не лента |
| RZD-Partner | `rzd-partner.ru/rss/` | 404 | нет RSS |
| Logirus | `logirus.ru/rss/news/` | 404 | нет RSS |

Отраслевые металлургия, логистика и нефтегаз покрываются общими деловыми лентами (тегер распознаёт отрасли
по словарям). Публичные RSS отраслевых изданий — в `docs/BACKLOG.md`.

## Правила сбора

- **User-Agent** — `NEWS_USER_AGENT` (по умолчанию `AlfaFutureBot/0.1 (+hackathon prototype…)`).
- **robots.txt** проверяется для каждого хоста и кэшируется на 24 ч. По RFC 9309: 4xx — ограничений нет,
  5xx и сетевая ошибка — считаем, что доступ запрещён.
- **Rate limit** — не чаще одного запроса в `NEWS_MIN_INTERVAL_S` (2 с) к одному хосту; не больше
  `NEWS_FETCH_CONCURRENCY` (4) источников параллельно; период опроса — `NEWS_POLL_INTERVAL_MIN` (15 мин).
- **Условные запросы** (ETag / If-Modified-Since); тело, совпадающее по SHA-256 с уже сохранённым, повторно
  не разбирается (`outcome = unchanged`).
- Ответ больше 5 МБ отбрасывается; таймаут — 20 с.

## Здоровье источников и резерв

Каждый опрос пишется в `core.news_raw`: исход, статус, задержка, число записей, новых и дублей.
`core.sources.health_status`:
- `healthy` — последний опрос успешен;
- `degraded` — 1–2 ошибки подряд;
- `down` — `NEWS_FAILURES_TO_DOWN` (3) ошибок подряд, либо `robots.txt` запретил доступ.

Когда основной источник `down`, в том же цикле опрашиваются его резервы (`fallback_for`). После восстановления
основного резерв снова не опрашивается.

## Сырые данные и повторная обработка

Тело ответа сохраняется в S3: `news-raw/<source>/<YYYY>/<MM>/<DD>/<HHMMSS>_<sha256[:16]>.xml`; новость ссылается
на свою выгрузку через `news_items.raw_id`. Повторная обработка без скачивания (например, после исправления парсера
или тегера) — `python scripts/ingest.py news --reprocess <raw_id> …`.

## Как добавить источник

1. Проверить ленту и `robots.txt`.
2. Добавить запись в `backend/data/reference/news_sources.json` (`id`, `name`, `kind`, `url`, `priority`, `topics`;
   при необходимости `fallback_for`).
3. Для нового вида источника (не RSS/Atom) — класс с методом `parse()` в `app/integrations/news/connectors.py`
   и запись в `CONNECTORS`.
4. Прогнать `make ingest-news`; проверить `core.news_raw` и `core.sources`.
