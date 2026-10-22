# CHANGELOG

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — semver.

## [Unreleased]

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
