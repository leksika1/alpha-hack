# backend — бэкенд «Альфа-Будущее»

Python 3.12, FastAPI, PostgreSQL 16 + pgvector, Redis, Taskiq и MinIO. Бэкенд хранит данные компаний,
собирает новости, индексирует аналитику банка, отбирает кандидатов, вызывает ML и проверяет результат.
Публичное API обслуживает фронтенд; внутренние Tools API и LLM Gateway обслуживают агента.

Полная документация всего проекта — [PROJECT_GUIDE](../docs/PROJECT_GUIDE.md); общий запуск с фронтендом
и настоящим ML — [корневой README](../README.md). Ниже — руководство для разработки бэкенда.

## Структура и слои

| Путь | Ответственность |
|---|---|
| `app/api/` | HTTP, авторизация, схемы запросов/ответов, SSE и RFC 7807 |
| `app/services/` | Прикладные сценарии, оркестрация загрузок, матчинга, генерации и вопросов |
| `app/domain/` | Контракты, признаки, правила, скоринг и валидаторы без зависимости от HTTP |
| `app/integrations/` | ML/LLM, эмбеддинги, OCR/PDF, новости, внешние данные |
| `app/repositories/` | ORM-модели; миграции хранятся отдельно |
| `app/core/` | Конфигурация, безопасность, БД, S3, логи и rate limit |
| `app/workers/` | Taskiq broker, worker tasks и расписания |
| `ml_stub/` | Детерминированная HTTP-реализация ML-контракта |
| `migrations/` | Миграции Alembic, единственный путь изменения схемы |
| `scripts/` | Seed, ingest, синтетика, экспорт OpenAPI и notices |
| `tests/` | Unit, integration, e2e, e2e_ml и live-проверки |
| `data/reference/`, `data/synthetic/` | Справочники и демо-данные в Git |

Основное направление зависимостей: API → services → domain/integrations/repositories.
Исходные данные организаторов находятся в корневом `data/raw/`, смонтированы только для чтения
и не коммитятся; схемы и примеры интеграции находятся в общем `contracts/`.

## Отдельный запуск бэкенда в Docker

Из корня репозитория один раз скопируйте `.env.example` в `.env`. Для самостоятельного стека без
сервиса `ml` задайте в этом файле:

```dotenv
SHARED_NETWORK_EXTERNAL=false
SHARED_NETWORK=alfa-backend-local-shared
BACKEND_ALIAS=alfa-backend-local
ML_ALIAS=alfa-ml-local
INSIGHT_GENERATOR=stub
ML_SERVICE_URL=http://ml-stub:8100
INTERNAL_API_URL=http://api:8000
EMBEDDING_PROVIDER=fake
NEWS_LIVE_SOURCES_ENABLED=false
NEWS_FIXTURES_ENABLED=true
MARKET_DATA_ENABLED=false
```

Затем из `backend/`:

```bash
cd backend
docker compose --env-file ../.env -p alfa-backend-local config -q
docker compose --env-file ../.env -p alfa-backend-local up -d --build
curl -fsS http://localhost:8000/ready
```

Здесь Compose автоматически добавляет `docker-compose.override.yml` с локальными портами. Миграции,
создание бакетов и seed выполняются перед API; генерации продолжаются в воркере. Это демо на правилах
и синтетике, которое после сборки образов не требует RSS/LLM/загрузки моделей.
Для настоящего ML используйте общий стек или доступный из контейнеров адрес в `ML_SERVICE_URL`,
`INSIGHT_GENERATOR=remote` и совпадающий `ML_SERVICE_TOKEN`. `INTERNAL_API_URL` должен быть доступен агенту.

Порты: API `8000`, Swagger `/docs`, ml-stub `8100`, S3 `9000`, консоль MinIO `9001`.
При параллельном запуске других стендов измените порты, сеть и алиасы. Для остановки отдельного стека
используйте ту же команду Compose с `down`; тома сохраняются. Не смешивайте его с командами корневого
`make up` без учёта разных имён проектов Compose.

## Локальный Python и окружение

```bash
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload --port 8000
```

Для API нужны запущенные PostgreSQL, Redis, S3, миграции и seed. Их можно оставить в Docker,
остановив контейнер API перед запуском локального процесса на том же порту. При необходимости отдельно
запускаются `uv run taskiq worker app.workers.broker:broker app.workers.tasks` и
`uv run taskiq scheduler app.workers.broker:scheduler app.workers.tasks`.

`Settings` читает переменные процесса и `.env` из текущей рабочей папки. Поэтому локальный `uv` из
`backend/` читает `backend/.env`, а Compose с `--env-file ../.env` — корневой файл. Они не подхватывают
настройки друг друга автоматически. Локальный Python должен использовать адреса опубликованных портов:
`DATABASE_URL=postgresql+asyncpg://alfa:alfa@localhost:15432/alfa`, `REDIS_URL=redis://localhost:16379/0`,
`S3_ENDPOINT_URL=http://localhost:19000`; пароль и порты должны соответствовать вашему стенду.

Токены `ML_SERVICE_TOKEN`, `SERVICE_TOKEN` и `JWT_SECRET` также согласуются с вызывающими сервисами.
Если локальный API вызывает ML в контейнере, задайте `INTERNAL_API_URL`, по которому контейнер сможет
вернуться к API на хосте: его `localhost` указывает на сам контейнер. Все реальные секреты — только
в окружении или исключённом из Git `.env`.

## Миграции и загрузка данных

На локальном Python из `backend/`: `uv run alembic upgrade head`. В общем Docker-стеке из корня —
`make migrate`; в отдельном стеке — `docker compose --env-file ../.env -p alfa-backend-local run --rm migrate`
из `backend/`. Не запускайте две команды миграций одновременно.

Миграция `0011` расширяет `core.candidate_matches.scorer` с 32 до 255 символов для полного имени
ML-скорера. Её нужно применить перед использованием обновлённого remote-scoring. Downgrade обратно
до 32 символов не обрезает имена молча: PostgreSQL откажет, если в базе уже есть длинные значения.

Для общего стека из корня: `make seed`, `make ingest-companies`, `make ingest-news`, `make ingest-analytics`.
Для отдельного стека используйте соответствующий `docker compose ... exec api python scripts/ingest.py`
с аргументом `companies`, `news --enqueue` или `analytics`. Источник задаётся `DATA_DIR`; PDF загружаются
из `analytics/` внутри каталога данных. Подробности и правила очистки — [DATA.md](../docs/DATA.md).

## Связка с ML и деградация

Бэкенд передаёт агенту задачу, run-токен одной компании, адреса инструментов и Gateway. Инсайт проходит
JSON Schema и бизнес-валидацию; отклонения сохраняются отдельно и не попадают в ленту.

- `ML_REQUEST_TIMEOUT_S=60` — одна HTTP-попытка; `ML_MAX_RETRIES=2` — повторы транспортных ошибок
  и HTTP 502/503/504. Run-токен действует на весь срок попыток и пауз.
- Один запуск имеет уникальный `request_id`; HTTP-повторы сохраняют его. Перегенерация создаёт новый
  запуск и ключ, чтобы не вернуть устаревший результат из кэша ML.
- `QA_TIMEOUT_S=90` — общий предел вопроса, ожидание HTTP и срок run-токена. Некорректный SSE-финал
  возвращает `error: invalid-answer`; некорректный JSON генерации завершает запуск как `failed`.
- `SCORER_PROVIDER=remote` перекалибровывает baseline-shortlist через `/v1/score`: только пары выше
  `MATCH_MIN_SCORE`, до `MATCH_MAX_PER_COMPANY` на компанию и роль. Батчи не больше 500, разделены по ролям.
  Проверяются идентификаторы и числа; порядок ответа не важен. При отказе батч остаётся на baseline.
  Старые карточки пары, опущенной ниже порога, снимаются. По умолчанию скорер — `baseline`.

Текущий `/v1/score` ML выполняет перекалибровку baseline; семантический `ml-ranker@2` работает внутри
агента генерации. Внешние LLM вызываются только через Gateway бэкенда; при исчерпании бюджета агент
использует правила. Подробный протокол — [INTEGRATION_ML.md](../docs/INTEGRATION_ML.md).

## Проверки и документация

Из корня: `make lint`, `make typecheck`, `make test`, `make integration`; всё вместе — `make check`.
Integration требует Docker для testcontainers. Прямой unit-прогон из `backend/`:

```bash
uv run pytest -m "not integration and not e2e and not live" -q
```

`make e2e` проверяет общий API-сценарий с `ml-stub`; `make e2e-ml` — HTTP-связку с настоящим ML
без платной LLM и семантических моделей. `make smoke-live` требует работающий стенд, сеть и настроенное
локальное окружение с совпадающим сервисным токеном; он расходует внешний бюджет. Наличие команды
не означает, что проверка уже выполнена на вашем окружении.

После изменения публичного API выполните `make openapi`; общие схемы меняются с ADR, записью в changelog
контрактов и обновлением версий/примеров. Полезные указатели: [API](../docs/API.md),
[тестирование](../docs/TESTING.md), [эксплуатация и диагностика](../docs/RUNBOOK.md),
[архитектура](../docs/ARCHITECTURE.md), [контракты](../contracts/README.md).
