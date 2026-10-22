# backend — бэкенд «Альфа-Будущее»

Python 3.12 / FastAPI / PostgreSQL + pgvector / Redis + Taskiq / MinIO. Общий README, контракты и документация —
в корне репозитория (`../README.md`, `../contracts/`, `../docs/`).

```bash
cd backend
uv sync --frozen        # зависимости из uv.lock
uv run pytest -m "not integration and not e2e and not live"
```

Структура: `app/` (api, core, domain, services, repositories, workers, integrations), `ml_stub/` (детерминированная
реализация ML-контракта), `migrations/` (Alembic), `scripts/` (ingest, seed, synthetic, openapi, notices),
`tests/` (unit, integration, e2e), `data/synthetic/` и `data/reference/` (коммитятся). Данные организаторов —
в корневом `data/raw/` (не коммитятся).
