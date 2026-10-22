# CLAUDE.md — правила работы в репозитории бэкенда «Альфа-Будущее»

> Начало каждой сессии и после сжатия контекста: **сначала прочитай этот файл и `docs/PROGRESS.md`**.
> Детали: `docs/agent/RULES.md` (процесс, DoD, когда останавливаться), `docs/agent/CONVENTIONS.md` (код).

## 1. Миссия и границы

Этот репозиторий — **бэкенд** сервиса персонализированных бизнес-инсайтов для «Альфа-Бизнес»:
данные о компаниях, сбор новостей, аналитика банка (OCR → RAG), инфраструктура ML-агента
(Tools API, LLM Gateway, трассировка), публичное API для фронтенда, безопасность, метрики, тесты, документация.

**Не делаем:** фронтенд (только OpenAPI-контракт, CORS) и ML-логику (промпты, логика агента,
обучение/выбор моделей, NER, генерация текста инсайтов). Для ML делаем контракты, `ml_stub`
и интерфейсы `EmbeddingProvider` / `RelevanceScorer` / `InsightGenerator` (`fake` / `baseline` / `remote`).

- Каталоги `frontend/` и `ml/` (если появятся) **не трогать**.
- `contracts/` меняются **только** через ADR в `docs/DECISIONS.md` + `contracts/CHANGELOG.md` + bump `schema_version`.
- Пограничная задача (например, классификация новостей) → baseline на правилах за интерфейсом + ADR «ML может заменить».

## 2. Источник правды

`docs/SPEC.md` — единственный источник правды. Перед задачей сверься со спекой.
Пробел в спеке → прими разумное решение, запиши ADR в `docs/DECISIONS.md`, продолжай.

## 3. Трекер прогресса

`docs/PROGRESS.md` — фазы, задачи, статусы, текущая задача, блокеры, следующий шаг.
Обновлять после каждой завершённой задачи. Идеи «на потом» и рефакторинг вне задачи → `docs/BACKLOG.md`.

## 4. Фокус

Одна задача за раз. Не переписывать работающее без причины. Новая зависимость — только с обоснованием
(в коммите/ADR) и записью в `THIRD_PARTY_NOTICES.md` (`make notices`).

## 5. Definition of Done (каждая задача)

1. Код + тесты (unit; integration для БД/S3/очередей).
2. `make check` зелёный (lint + typecheck + unit + integration).
3. Документация и `docs/openapi.json` обновлены (`make openapi`), если менялось API.
4. `docs/PROGRESS.md` обновлён.
5. Коммит: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`), маленький и осмысленный.

## 6. Когда останавливаться и спрашивать человека

Только в этих случаях (во всех остальных — решай сам и продолжай):
- необратимые действия: удаление данных, `push --force`, переписывание истории;
- нужны секреты/ключи (`LLM_API_KEY` и т.п.);
- изменение границ ответственности или контрактов, уже переданных команде (ML/фронт);
- противоречие в требованиях кейса, меняющее архитектуру.

## 7. Запреты

- Не коммитить секреты, `.env`, сырые данные организаторов (`data/raw/`).
- Не вызывать LLM вне белого списка и мимо LLM Gateway (`/internal/llm/*`).
- Не выдумывать данные о компаниях: недостающее — `NULL` + флаг в `data_completeness`.
- Не отключать и не ослаблять тесты, чтобы они прошли.
- Не мокать в боевом пути: моки только за флагом (`*_PROVIDER=fake`, `NEWS_FIXTURES_ENABLED`) или в тестах.
- Не оставлять `TODO` без записи в `docs/BACKLOG.md`.

## 8. Конвенции (кратко; подробно — `docs/agent/CONVENTIONS.md`)

- Код и идентификаторы — английский; документация — русский.
- Структура: `app/{api,domain,services,repositories,workers,integrations,core}`; `ml_stub/` — отдельный сервис.
- `app/domain` не зависит от FastAPI/SQLAlchemy. mypy strict для `app/domain` и `app/services`.
- Конфиги — `pydantic-settings` (`app/core/config.py`), секреты только из env.
- Время в БД — UTC (`timestamptz`); деньги — `numeric(20,2)`; ошибки API — RFC 7807 `application/problem+json`.
- Python 3.12, uv (`uv.lock`), FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, Taskiq + Redis.

## 9. Команды

| Команда | Что делает |
|---|---|
| `make up` / `make down` / `make logs` | поднять / остановить / логи compose (профиль default) |
| `make migrate` | `alembic upgrade head` |
| `make seed` | демо-пользователи, справочники, предгенерация инсайтов |
| `make synthetic` | сгенерировать `data/synthetic/` (детерминированно) |
| `make ingest-companies` / `ingest-news` / `ingest-analytics` | загрузка данных |
| `make test` | unit-тесты |
| `make integration` | integration-тесты (testcontainers) |
| `make e2e` | весь compose в профиле `test` + `tests/e2e` |
| `make check` | lint + typecheck + unit + integration |
| `make openapi` | выгрузить `docs/openapi.json` |
| `make notices` | сгенерировать `THIRD_PARTY_NOTICES.md` из `uv.lock` |

Без `make` (Windows): эквивалентные команды — в `Makefile`, запускать через `uv run ...`.

## 10. Самопроверка перед завершением фазы

Чек-лист фазы в `docs/PROGRESS.md` → `make check` и `make e2e` → сверка таблицы трассировки в `docs/SPEC.md` §3.

## Карта документации

`docs/SPEC.md` · `docs/ARCHITECTURE.md` · `docs/DATA.md` · `docs/DATA_MODEL.md` · `docs/API.md` ·
`docs/IMPACT_SCALE.md` · `docs/SECURITY.md` · `docs/METRICS.md` · `docs/SOURCES.md` · `docs/DECISIONS.md` ·
`docs/TESTING.md` · `docs/RUNBOOK.md` · `docs/INTEGRATION_ML.md` · `docs/INTEGRATION_FRONTEND.md` · `contracts/README.md`
