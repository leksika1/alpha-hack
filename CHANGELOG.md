# CHANGELOG

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — semver.

## [Unreleased]

## [1.0.0] — 2026-10-23

Первая версия бэкенда для сдачи: фазы 0–8 (`docs/REPORT.md`).

### Добавлено (фаза 8 — сдача)
- README со сценарием проверки для жюри; `scripts/demo_check.py` проходит путь пользователя по API.
- `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/RUNBOOK.md`, `docs/REPORT.md`; live smoke (`make smoke-live`).

### Добавлено (фаза 7 — e2e)
- Все 12 сценариев SPEC §11.2 в e2e; `docs/TESTING.md` с картой сценариев и тестов.
- `POST /api/v1/admin/news` — ручной ввод новости (пресс-релиз, сообщение аналитика) через общий конвейер.

### Добавлено (фаза 6 — доставка по CJM)
- Уведомления: срочные инсайты, дайджест дня/недели, статусы заявок; список и SSE-поток с `Last-Event-ID`.
- `GET /digest`; вопросы к аналитике `POST /insights/{id}/ask`, `POST /ask` (SSE, проверка ответа агента, история).
- Карточка: «почему это для вас», причины приоритета, отметка «обновлено»; «наблюдать»; ответ на отзыв.
- `GET /me/requests`; admin: метрики north star и этапов CJM, качество данных, здоровье источников, запуск сбора,
  перегенерация, статусы заявок; `docs/METRICS.md`.

### Добавлено (фаза 5 — инфраструктура агента)
- LLM Gateway `/internal/llm`: белый список из 6 моделей, дневной бюджет в ₽ и токенах с резервированием (жёсткий
  стоп и при параллельных вызовах), маскирование PII, кэш детерминированных запросов, журнал `llm_usage`, стриминг.
- `GET /api/v1/admin/llm-usage`; вызовы LLM в трассировке инсайта (`llm_calls`); агенту передаётся `allow_llm=false`
  при исчерпанном бюджете.
- Rate limit (Redis token bucket) для `/api/v1` и `/internal`; запрет запуска `APP_ENV=prod` с dev-секретами.
- Матчинг события по всей клиентской базе через предфильтр по индексу (`MATCH_SCOPE`), худшее событие на данных
  организаторов — 3,0 с.

### Добавлено (фаза 4 — аналитика банка)
- Загрузка PDF аналитики: S3 по sha256, текстовый слой или OCR Tesseract (rus+eng) с кэшем результатов в S3,
  чанкинг по разделам со страницами, метаданные (название, дата, темы).
- Эмбеддинги за `EmbeddingProvider` (`fake`, `local` — fastembed, `remote`; модель выбирает ML-команда) и переиндексация.
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
