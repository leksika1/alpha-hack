# PROGRESS — трекер работ бэкенда

Статусы: `[ ]` не начато · `[~]` в работе · `[x]` готово · `[!]` блокер

**Дедлайн:** _не указан (запрошен у человека)_ · **Репозиторий:** `git@github.com:leksika1/alfa-hack.git`

## Текущее состояние

- **Текущая фаза:** 2 — Ядро API и сквозной скелет
- **Текущая задача:** —
- **Блокеры:** нет жёстких. Открытые вопросы — см. «Открытые вопросы».
- **Следующий шаг:** Фаза 2 — auth, демо-учётки, API компаний и профиля.

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

## Фаза 2 — Ядро API и сквозной скелет (главный рубеж)

- [ ] Auth (JWT, демо-учётки, refresh), `/me`, user_company_access, роли
- [ ] Компании: список, профиль, PATCH профиля, граф
- [ ] Лента (курсор, фильтры, роль), инсайт, state, feedback, source-click
- [ ] Действия → мок-CRM (`crm_requests`), каталог `bank_products`
- [ ] ml_stub: `/v1/insights/generate`, `/v1/chat` (SSE), `/v1/score` — детерминированно по контракту
- [ ] Seed: демо-пользователи + предгенерация инсайтов
- [ ] Сквозной сценарий на синтетике работает

## Фаза 3 — Новости

- [ ] Проверка реальных RSS/API → `sources`, `docs/SOURCES.md`
- [ ] Плагинные коннекторы (RSS, JSON API, HTML), robots.txt, UA, rate limit
- [ ] Raw → S3, нормализация, повторная обработка из S3
- [ ] Дедуп (канон URL + хэш + near-dup по эмбеддингу), кластеризация в события
- [ ] Baseline-тегирование (ОКВЭД-секции, страны, регионы, сущности, тип события), срочность
- [ ] Health-check и резервные источники, метрики
- [ ] Планировщик (Taskiq scheduler), fixture-источник

## Фаза 4 — Аналитика банка и векторный поиск

- [ ] PDF → S3 → текстовый слой или OCR (Tesseract rus; опционально VLM через Gateway) → страницы
- [ ] Чанкинг (заголовок, дата, страница, раздел) → эмбеддинги → pgvector (HNSW) + tsvector
- [ ] Гибридный поиск (вектор + BM25 + RRF) с цитатами и страницами
- [ ] Presigned URL на документ с `#page=N`

## Фаза 5 — Инфраструктура агента

- [ ] Tools API (scoping по run_id, read-only, аудит)
- [ ] LLM Gateway (белый список, бюджет, кэш, маскирование, llm_usage)
- [ ] Очередь генерации, agent_runs/agent_steps, трассировка
- [ ] Валидатор инсайтов (схема + бизнес-правила) → insights / insights_rejected
- [ ] Matching events × companies + baseline RelevanceScorer с breakdown; роли

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

1. Дедлайн (дата/время) — не указан в задании.
2. `LLM_BASE_URL` / `LLM_API_KEY` организаторов — нужны для `make smoke-live` (без них всё работает на ml_stub).
3. Модель эмбеддингов — согласовать с ML (по умолчанию `intfloat/multilingual-e5-small`, 384).
4. OCR аналитики: Tesseract (локально, по умолчанию) vs Qwen3-VL через Gateway (качество таблиц/графиков, тратит бюджет).
5. Официальные образы MinIO больше не публикуются — используем `pgsty/minio` (ADR-004). Ок?
6. `materials/` добавлен в `.gitignore` (материалы организаторов). Коммитить их в репозиторий?

## Журнал

- 2026-10-22 — Фаза 1: данные компаний (ingest, граф, контекст, синтетика, демо-выбор). make check + e2e зелёные. Ветка `dev`.

- 2026-10-22 — Фаза 0: профилирование данных (PDF — сканы → OCR), правила, SPEC, контракты v1.0.0, каркас, compose, CI. make check + e2e зелёные.
