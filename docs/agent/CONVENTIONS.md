# Конвенции кода

## Структура

```
app/
  api/            # FastAPI: роутеры /api/v1, /internal/tools, /internal/llm; зависимости; problem+json
  core/           # config (pydantic-settings), logging (structlog), security (JWT), db, s3, redis, metrics
  domain/         # чистая логика и модели: контракты инсайта, валидатор, признаки, скоринг (без FastAPI/SQLAlchemy)
  services/       # прикладные сценарии (оркестрация репозиториев, интеграций и домена)
  repositories/   # доступ к БД (SQLAlchemy 2 async), только SQL/ORM
  workers/        # Taskiq broker, задачи, планировщик
  integrations/   # внешние системы: LLM, ML-сервис, новостные коннекторы, эмбеддинги, OCR, CRM-мок
ml_stub/          # отдельный сервис: детерминированная реализация contracts/ml-service.openapi.yaml
migrations/       # Alembic
scripts/          # CLI: ingest, synthetic, seed, profiling, notices, openapi
contracts/        # контракты с ML (JSON Schema, OpenAPI) + examples
tests/{unit,integration,e2e,fixtures}
```

## Стиль

- Python 3.12, `ruff` (lint + format, line-length 110), `mypy --strict` для `app/domain`, `app/services`.
- Идентификаторы и комментарии в коде — английский. Документация — русский.
- Pydantic v2 для всех DTO. `from __future__ import annotations` не используется в моделях Pydantic/SQLAlchemy.
- Асинхронность везде, где I/O. Синхронные тяжёлые вычисления (OCR, эмбеддинги, pandas) — в воркере/`to_thread`.
- Зависимости между слоями: `api → services → (repositories, integrations, domain)`; `domain` ни от чего не зависит.
- Интерфейсы-провайдеры — `typing.Protocol` в `app/domain/ports.py`; реализации — `fake` / `baseline` / `remote`
  в `app/integrations/`, выбор через env (`EMBEDDING_PROVIDER`, `SCORER_PROVIDER`, `INSIGHT_GENERATOR`, `OCR_PROVIDER`).

## Данные

- Время в БД — `timestamptz` UTC. Деньги — `numeric(20,2)` (рубли). Доли/скоры — `double precision`.
- ID компаний — исходные `SYNTHETIC_U` (`U_123`) как `text` PK; внутренние сущности — `uuid` (v7 где важна сортировка) или `bigint identity`.
- Каждый признак `company_context` хранит `source` ∈ {csv, derived, user, external}.
- Миграции — только Alembic; ручные DDL запрещены. Тяжёлые загрузки — `COPY`.

## API

- Префикс `/api/v1`. Ошибки — RFC 7807 (`application/problem+json`, поля `type,title,status,detail,instance,trace_id`).
- Пагинация — курсор (`?cursor=&limit=`, ответ `{items, next_cursor}`).
- Каждый эндпоинт: response_model, примеры, перечисленные коды ошибок.
- Внутренние API: `/internal/tools/*` (сервисный токен + `X-Run-Id`), `/internal/llm/*` (сервисный токен).

## Логи и наблюдаемость

- structlog JSON; поля `request_id`, `trace_id`, `user_id` (хэш), `run_id`. PII не логируется.
- Prometheus `/metrics`; `/health` (liveness), `/ready` (БД, Redis, S3).

## Git

- Conventional Commits. Маленькие коммиты. Ветка по умолчанию `main`; теги `vX.Y.Z`.
