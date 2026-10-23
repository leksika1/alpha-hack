# PROGRESS — трекер работ бэкенда

Статусы: `[ ]` не начато · `[~]` в работе · `[x]` готово · `[!]` блокер

**Дедлайн:** 26 октября, 18:00 (сообщение организаторов в командном чате; часовой пояс не уточнён) · **Репозиторий:** `https://github.com/leksika1/alpha-hack`

## Текущее состояние

- **Текущая фаза:** 6 — Доставка и взаимодействие
- **Текущая задача:** —
- **Блокеры:** нет жёстких. Открытые вопросы — см. «Открытые вопросы».
- **Следующий шаг:** Фаза 6 — SSE-уведомления, дайджесты, вопросы к аналитике (SSE), admin-метрики.

## Фаза 0 — Основа

- [x] Прочитать `materials/` (кейс, FAQ), профилировать `data/raw/` → `docs/DATA.md`
- [x] Правила агента: `CLAUDE.md`, `.claude/`, `docs/agent/`
- [x] `docs/PROGRESS.md`, `docs/BACKLOG.md`, `docs/DECISIONS.md` (ADR-001…)
- [x] `docs/SPEC.md` (полная спецификация + трассировка)
- [x] `docs/IMPACT_SCALE.md` (нужна для контракта)
- [x] `contracts/`: insight.schema.json, ml-service.openapi.yaml, tools-api.openapi.yaml, llm-gateway.openapi.yaml, examples, README, CHANGELOG
- [x] Зеркальные Pydantic-модели контракта + тест соответствия схемы и примеров
- [x] Каркас репозитория (`app/`, `ml_stub/`, `tests/`), `pyproject.toml`, `uv.lock`
- [x] docker-compose (api, worker, scheduler, postgres+pgvector, redis, minio, minio-init, ml-stub, migrate, seed), Dockerfile, `.env.example`
- [x] Makefile, CI (GitHub Actions)
- [x] `make check` зелёный (unit 49, integration 3), `make e2e` зелёный (compose-стек, 3 smoke), коммит
- [x] Сводка человеку: стек, границы, контракты, открытые вопросы

## Фаза 1 — Данные компаний

- [x] Alembic 0002: `staging.*_raw`, core: companies, company_groups, okved_dict, company_fts_countries, company_products, persons, person_company_links, payments_agg, company_edges, company_context, ingest_runs, data_quality_report (тест на отсутствие дрейфа ORM ↔ миграции)
- [x] Ingest CSV → staging через `COPY`, очистка → core, отчёт качества (дубли, заглушки, пропуски, рассогласование ФТС, висячие ссылки, самопереводы)
- [x] Справочник ОКВЭД (`data/reference/okved_classes.csv` + `okved_overrides.csv`): секции, отраслевые теги, сырьё, флаги
- [x] Граф: payment, shared_person, гипотезы key_supplier / key_customer / affiliated_outside_group (confidence + evidence), ADR-015
- [x] `company_context`: признаки с источником, `data_completeness`, HHI, топ-10 контрагентов, доли отраслей, чувствительности; пересчёт одной компании
- [x] Синтетический генератор `make synthetic` (детерминированный, воспроизводит особенности реальных данных) + `data/synthetic/`
- [x] Выбор демо-компаний (`scripts/select_demo_companies.py`), seed грузит компании при пустой БД
- [x] Тесты: unit (ОКВЭД, очистка, контекст, синтетика), integration (ingest на Postgres, идемпотентность, правки пользователя, схема)
- [x] Прогон на реальных данных: 85 513 компаний, 1,38 млн платежей, 439 тыс. рёбер — 151 с

## Фаза 2 — Ядро API и сквозной скелет (главный рубеж) — готово

- [x] Auth: JWT access/refresh (pyjwt, scrypt-хэши), демо-учётки, `/me`, `user_company_access`, ручная смена роли
- [x] Компании: список, профиль (признаки по разделам + источник + полнота), PATCH анкеты → пересчёт контекста → перематчинг → генерация, граф (гипотезы помечены)
- [x] Лента (одна карточка на событие, вариант по роли, фильтры, период, курсор), `/feed/summary` для виджета, инсайт, trace, state, feedback, source-click
- [x] Действия → мок-CRM (`crm_requests`), каталог `bank_products` (`backend/data/reference/bank_products.json`)
- [x] ml_stub: `/v1/insights/generate` (агент ходит в Tools API, пишет шаги, самопроверка по схеме), `/v1/chat` (SSE), `/v1/score`
- [x] Tools API (`/internal/tools/*`): run-токен, scope компании, аудит каждого вызова, бюджет вызовов на запуск
- [x] Валидатор инсайтов (JSON Schema + бизнес-правила) → `insights` / `insights_rejected`; `agent_runs/agent_steps`
- [x] Baseline: тегер новостей (словари, тип события, направление, срочность, prompt injection), скорер с breakdown и весами ролей, шкала влияния
- [x] Seed: продукты, демо-учётки, демо-новости, матчинг, предгенерация через очередь (холодный старт)
- [x] Сквозной сценарий на синтетике: integration `test_api_flow.py` (12) + e2e на compose (8)
- [x] Репозиторий разделён на `backend/`, `ml/`, `frontend/` (ADR-018)

## Фаза 3 — Новости — готово

- [x] Проверка реальных RSS (22 кандидата + 17 альтернатив) → каталог из 16 источников, `docs/SOURCES.md` (исключённые с причинами)
- [x] Коннекторы-плагины: `RssConnector` (RSS/Atom/RDF, кодировки, HTML → текст), `FixtureConnector`; вежливый загрузчик: robots.txt (RFC 9309), User-Agent, интервал на хост, ETag/If-Modified-Since, лимит размера
- [x] Сырьё → S3 (`news-raw`), `core.news_raw` (исход, статус, задержка, счётчики), `news_items.raw_id`; повторная обработка из S3 без скачивания; неизменённое тело не разбирается
- [x] Дедуп (канон URL, хэш текста, почти-дубли) и склейка в события (текст + тип + теги, ±72 ч), ADR-021; проверено на живых данных
- [x] Baseline-тегирование, срочность; исправлен омоним «стали» — фаза 2/3
- [x] Здоровье источников (healthy/degraded/down) и автоматический опрос резервных
- [x] Планировщик: задача `news.poll` каждые `NEWS_POLL_INTERVAL_MIN`, после сбора — матчинг и генерация в очереди; CLI `scripts/ingest.py news`; демо-лента через тот же конвейер (ADR-022)
- [x] Тесты: unit (склейка, коннекторы, загрузчик на respx), integration (конвейер на Postgres + MinIO), e2e

## Фаза 4 — Аналитика банка и векторный поиск — готово

- [x] PDF → S3 (по sha256) → текстовый слой (pypdfium2) или OCR (Tesseract rus+eng, 300 dpi, кэш в S3) → страницы, ADR-023; VLM — BACKLOG
- [x] Чанкинг (разделы, колонтитулы, страница, перекрытие) и метаданные (название, дата, темы) → эмбеддинги (fake/local/remote) → pgvector (HNSW) + tsvector (GIN), ADR-024
- [x] Гибридный поиск (вектор + BM25 + RRF k=60) с цитатами, страницами, методом извлечения и breakdown; фильтры по дате/темам/документам; Tools API `search_bank_analytics`
- [x] Presigned URL на документ с `#page=N`: `GET /analytics/documents`, `/download`, переход к источнику `bank_analytics`
- [x] Синтетические демо-PDF с текстовым слоем (помечены «ДЕМО (синтетика)»); seed загружает аналитику до предгенерации; CLI `scripts/ingest.py analytics [--force|--reembed]`, задача воркера `analytics.ingest`
- [x] Тесты: unit (чанкинг, метаданные, TSV Tesseract, эмбеддинги), integration (конвейер, поиск, фильтры, смена модели, presigned, OCR-путь с кэшем, реальный Tesseract), API-поток, e2e

## Фаза 5 — Инфраструктура агента — готово

- [x] Tools API (scoping по run_id, read-only, аудит) — сделано в фазе 2
- [x] LLM Gateway (ADR-028): белый список (403), дневной бюджет в ₽ и токенах с резервированием под advisory lock (жёсткий стоп и под конкуренцией), маскирование PII, кэш `temperature=0`, `llm_usage`, стриминг, `GET /api/v1/admin/llm-usage`; `allow_llm=false` агенту при исчерпании
- [x] Очередь генерации, agent_runs/agent_steps — фаза 2; трассировка дополнена `llm_calls`
- [x] Валидатор инсайтов (схема + бизнес-правила) → insights / insights_rejected — фаза 2
- [x] Matching: предфильтр по инвертированному индексу по всей базе (ADR-029, `MATCH_SCOPE`), компактные `scoring_features`; NFR-3 на данных организаторов — худшее событие 3,0 с; скорер уточнён (доля контрагентов от 5 %, бусты только с опорой на признак)
- [x] Безопасность: rate limit (Redis token bucket, fail open), запрет dev-секретов в `APP_ENV=prod`, `docs/SECURITY.md`
- [x] ml-stub: `ML_STUB_USE_LLM` — один вызов через Gateway, деградация на шаблон при 403/429/ошибке
- [x] Тесты: unit (маскирование, оценки, кэш-ключ, провайдер на respx, rate limit, prod-guard), integration (Gateway, бюджет под конкуренцией, rate limit, LLM в трассировке, деградация, надмножество предфильтра), e2e (403 модели, бюджет, `llm_calls` в трассировке)

## Фаза 6 — Доставка и взаимодействие

- [ ] SSE-уведомления, `/feed/summary`, дайджесты day/week
- [ ] Вопросы к аналитике (SSE) через ML/ml_stub
- [ ] Дополнение профиля → перерасчёт контекста → перегенерация
- [ ] Admin-эндпоинты, метрики (`/admin/metrics`, metrics_daily)

## Фаза 7 — E2E

- [ ] Сценарии 1–12 из SPEC §11, `make e2e`, CI зелёный

## Фаза 8 — Сдача

- [ ] README (сценарий для жюри), все docs, THIRD_PARTY_NOTICES, OpenAPI, Mermaid
- [ ] Запуск с нуля на чистой машине, тег версии, финальный отчёт

## Открытые вопросы к человеку

1. ~~Дедлайн~~ — 26 октября, 18:00 (из командного чата); часовой пояс не уточнён.
2. `LLM_BASE_URL` / `LLM_API_KEY` организаторов — нужны для `make smoke-live` (без них всё работает на ml_stub).
3. Модель эмбеддингов — согласовать с ML (по умолчанию `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384; e5-small нет в fastembed — ADR-006).
4. OCR аналитики: Tesseract (локально, по умолчанию) vs Qwen3-VL через Gateway (качество таблиц/графиков, тратит бюджет).
5. Официальные образы MinIO больше не публикуются — используем `pgsty/minio` (ADR-004). Ок?
6. `materials/` добавлен в `.gitignore` (материалы организаторов). Коммитить их в репозиторий?
7. Тарифы моделей (₽ за 1M токенов) для `LLM_PRICES` — до их получения бюджет считается по консервативной цене 100/300 ₽ (ADR-028).

## Журнал

- 2026-10-23 — Фаза 5: LLM Gateway (белый список, бюджет с резервом, маскирование, кэш, журнал), rate limit, матчинг по всей базе (NFR-3: 3,0 с), LLM в трассировке.
- 2026-10-23 — Обработка PDF (ADR-027): лимиты размера/страниц/рендера, ограниченное чтение файлов,
  сериализация PDFium, освобождение изображений, завершение Tesseract при отмене/тайм-ауте, корректный
  отказ CLI при частичных ошибках. `make check` зелёный: unit 248, integration 43 passed / 1 skipped
  (нет локального бинаря Tesseract). Этот OCR-тест отдельно пройден внутри Docker с настоящим Tesseract;
  `make e2e` на изолированном compose-стеке — 9 passed. OpenAPI перегенерирован, изменений схемы нет.
- 2026-10-23 — Коннекторы эмбеддингов (ADR-026): проверка и нормализация ответов, восстановление порядка,
  remote Bearer-токен, отказ от неявного `local → fake`, полнотекстовый поиск при недоступности модели.
  Проверки: ruff lint/format, mypy; unit 232, integration 43 passed / 1 skipped (локально нет Tesseract).
- 2026-10-23 — Надёжность аналитики (ADR-025): сериализация импорта одного PDF, сохранение UUID неизменившихся
  цитат и готового индекса при сбое, атомарный переэмбеддинг документа, отказ публикации пустых сканов.
  Проверки: ruff lint/format, mypy; unit 209, integration 41 passed / 1 skipped (локально нет Tesseract).
- 2026-10-23 — Фаза 4: аналитика банка — PDF → S3 → текст/OCR → чанки → эмбеддинги + tsvector → гибридный поиск с цитатами страниц, presigned-ссылки.
- 2026-10-22 — Фаза 3: сбор новостей из 16 реальных RSS (+ демо-лента), сырьё в S3, дедуп и склейка в события, здоровье и резерв, планировщик.

- 2026-10-22 — Фаза 2: сквозной сценарий (auth → лента → инсайт → trace → CTA) на синтетике; Tools API, ml-stub, валидатор, скорер. История dev переписана без AI-трейлеров (по запросу владельца), репозиторий разделён на backend/ml/frontend.

- 2026-10-22 — Фаза 1: данные компаний (ingest, граф, контекст, синтетика, демо-выбор). make check + e2e зелёные. Ветка `dev`.

- 2026-10-22 — Фаза 0: профилирование данных (PDF — сканы → OCR), правила, SPEC, контракты v1.0.0, каркас, compose, CI. make check + e2e зелёные.
