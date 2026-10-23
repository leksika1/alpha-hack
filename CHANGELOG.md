# CHANGELOG

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — semver.

## [Unreleased]

### Добавлено (фаза 4 — аналитика банка)
- Загрузка PDF аналитики: S3 по sha256, текстовый слой или OCR Tesseract (rus+eng) с кэшем результатов в S3,
  чанкинг по разделам со страницами, метаданные (название, дата, темы).
- Эмбеддинги за `EmbeddingProvider` (`fake`, `local` — fastembed MiniLM-L12 multilingual, `remote`) и переиндексация.
- Гибридный поиск (pgvector HNSW + полнотекстовый `russian` + RRF) с цитатами страниц для Tools API `search_bank_analytics`.
- `GET /api/v1/analytics/documents`, `GET /api/v1/analytics/documents/{id}/download` (presigned URL с `#page=N`);
  переход к источнику `bank_analytics` ведёт на страницу PDF.
- Синтетические демо-PDF (помечены «ДЕМО (синтетика)»), `make ingest-analytics`, задача воркера `analytics.ingest`.

### Добавлено (фаза 3 — новости)
- Каталог из 16 проверенных RSS (ЦБ, Интерфакс, РБК, Ведомости, Прайм, РИА, РГ, BFM, Forbes, Газета.ru, Retail.ru,
  New Retail, Zerno.ru, Финмаркет) с резервными источниками; `docs/SOURCES.md`.
- Вежливый сбор (robots.txt, User-Agent, интервал, условные запросы), сырьё в S3, повторная обработка из S3.
- Дедупликация и склейка новостей в события; здоровье источников и автоматическое переключение на резерв.
- Задача планировщика `news.poll` → матчинг → генерация инсайтов; `make ingest-news`.

### Добавлено (фаза 2 — сквозной сценарий)
- Публичное API v1: auth (JWT), `/me`, смена роли, компании (профиль с источниками признаков, анкета, граф), лента и виджет,
  инсайт, трассировка, состояние, обратная связь, переход к источнику, действия в мок-CRM, продукты, новости.
- Tools API для агента (run-токен, scope компании, аудит), `ml-stub` по контракту (генерация, чат SSE, скоринг).
- Валидатор инсайтов, скорер релевантности с breakdown и весами ролей, шкала влияния, тегер новостей, демо-лента новостей.
- Seed с демо-учётками и предгенерацией через очередь; сквозные integration- и e2e-тесты.

### Изменено
- Репозиторий разделён на `backend/`, `ml/`, `frontend/`; общие `contracts/` и `docs/` в корне (ADR-018).

### Добавлено (фаза 0 — основа)
- Правила работы агента (`CLAUDE.md`, `.claude/`, `docs/agent/`), трекер `docs/PROGRESS.md`, ADR-001…014.
- Профиль входных данных `docs/DATA.md` (в т.ч. находка: PDF аналитики — сканы, нужен OCR).
- Спецификация `docs/SPEC.md` с трассировкой на критерии 01–12 и ограничения О1–О6; шкала влияния `docs/IMPACT_SCALE.md`.
- Контракты v1.0.0 (draft): `contracts/insight.schema.json`, `ml-service`, `tools-api`, `llm-gateway` + примеры.
- Каркас FastAPI (`/health`, `/ready`, `/metrics`, problem+json, request/trace-id, JSON-логи с маскированием PII),
  Taskiq-воркер и планировщик, `ml-stub` (скелет), Alembic (pgvector, схемы `staging`/`core`).
- docker-compose (api, worker, scheduler, postgres+pgvector, redis, minio, minio-init, ml-stub, migrate, seed) + оверлей `test`,
  Makefile, GitHub Actions CI (lint, mypy, unit, integration, e2e).
