# Альфа-Будущее — бэкенд персонализированных бизнес-инсайтов

Сервис внутри интернет-банка «Альфа-Бизнес»: превращает поток внешних новостей и аналитику Альфа-Банка в
персонализированные инсайты для ЛПР среднего и крупного бизнеса — что произошло, почему это важно именно этой компании,
какие риски и возможности, что делать, как это оценивает банк и на чём основан вывод.

> **Статус:** фазы 0–1 готовы (основа, данные компаний), идёт фаза 2 (API и сквозной сценарий). Прогресс — [`docs/PROGRESS.md`](docs/PROGRESS.md).
> Полный сценарий проверки для жюри появится к фазе 8.

## Структура репозитория

| Папка | Чья зона | Что внутри |
|---|---|---|
| `backend/` | бэкенд | API, данные компаний, новости, аналитика, инфраструктура агента, `ml_stub`, тесты |
| `ml/` | команда ML | ML-сервис по `contracts/ml-service.openapi.yaml` |
| `frontend/` | команда фронтенда | интерфейс «Альфа-Бизнес» по `docs/openapi.json` |
| `contracts/` | общая | контракты между командами (JSON Schema, OpenAPI, примеры) |
| `docs/` | общая | спецификация, архитектура, данные, ADR |
| `data/raw/` | — | данные организаторов (не коммитятся) |

## Архитектура (кратко)

FastAPI (`api`) + Taskiq (`worker`, `scheduler`) + PostgreSQL 16/pgvector + Redis + MinIO (S3) + `ml-stub`
(детерминированная реализация контракта ML). Подробно — [`docs/SPEC.md`](docs/SPEC.md) §7, [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Требования

- Docker 24+ с Compose v2.24+ (≥ 4 ГБ RAM для Docker), ~5 ГБ диска.
- Для локальной разработки: [uv](https://docs.astral.sh/uv/) (Python 3.12 ставится автоматически), GNU make (Linux/macOS).

## Запуск

```bash
cp .env.example .env
make up
```

Без make (Windows): `docker compose up -d --build`. Проверка: http://localhost:8000/health, http://localhost:8000/ready,
Swagger — http://localhost:8000/docs, консоль MinIO — http://localhost:9001.

Данные организаторов (не входят в репозиторий) кладутся в `data/raw/` (см. [`docs/DATA.md`](docs/DATA.md));
без них система работает на синтетике `backend/data/synthetic/`.

## Команды

| make | без make |
|---|---|
| `make up` / `make down` | `docker compose up -d --build` / `docker compose down` |
| `make check` | `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -m "not integration and not e2e and not live" && uv run pytest -m integration` |
| `make e2e` | `docker compose -p alfa-e2e -f docker-compose.yml -f docker-compose.test.yml --profile test up --build --abort-on-container-exit --exit-code-from e2e-runner e2e-runner` |
| `make openapi` | `cd backend && uv run python scripts/export_openapi.py` |
| `make notices` | `cd backend && uv run python scripts/gen_notices.py` |

Все цели — `make help`. Переменные окружения — [`.env.example`](.env.example) (каждая описана).

## Документация

[`docs/SPEC.md`](docs/SPEC.md) (источник правды) · [`contracts/README.md`](contracts/README.md) (контракты с ML) ·
[`docs/INTEGRATION_ML.md`](docs/INTEGRATION_ML.md) · [`docs/INTEGRATION_FRONTEND.md`](docs/INTEGRATION_FRONTEND.md) ·
[`docs/DATA.md`](docs/DATA.md) · [`docs/IMPACT_SCALE.md`](docs/IMPACT_SCALE.md) · [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)

Материалы носят информационный характер и не являются инвестиционной рекомендацией.
