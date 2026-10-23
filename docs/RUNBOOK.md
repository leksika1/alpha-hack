# Эксплуатация (RUNBOOK)

Команды — из корня репозитория. Без `make` (Windows) — команды из `Makefile`, они в 1–2 строки.

## Запуск и остановка

| Действие | Команда |
|---|---|
| Поднять стенд | `cp .env.example .env` (один раз), затем `make up` (`docker compose up -d --build`) |
| Состояние | `make ps`; `curl localhost:8000/ready` — Postgres, Redis, бакеты S3 |
| Логи | `make logs`; одного сервиса — `docker compose logs -f worker` |
| Остановить (данные сохраняются) | `make down` |
| Начать с нуля (**удаляет данные**) | `docker compose down -v` |

Порядок старта задаёт compose: `postgres`, `redis`, `minio` → `minio-init` (бакеты) → `migrate` → `seed` →
`api`, `worker`, `scheduler`, `ml-stub`. Seed идемпотентен:
1. справочники;
2. компании — если таблица пуста, загружается `DATA_DIR`;
3. аналитика;
4. демо-учётки;
5. демо-лента;
6. матчинг;
7. постановка предгенерации в очередь.

Первые инсайты появляются через ~30–60 с после старта `worker`.

## Данные организаторов

1. Положить файлы в `data/raw/` (структура — `docs/DATA.md`). Папка смонтирована в контейнеры только для чтения
   и не коммитится.
2. В `.env` указать `DATA_DIR=data/raw`.
3. `make up` на пустой базе: seed сам загрузит компании (~2 мин на 85 тыс. компаний и 1,4 млн платежей) и выберет
   демо-компании.
4. На заполненной базе:
   - компании — `make ingest-companies`; повторная загрузка пересчитывает контекст;
   - аналитика — `make ingest-analytics`: сканы идут через OCR, ~8 мин на 64 страницы; результат кэшируется в S3,
     поэтому повтор занимает секунды;
   - принудительно переобработать — `docker compose exec api python scripts/ingest.py analytics --force`.
5. Новости: опрашиваются планировщиком каждые `NEWS_POLL_INTERVAL_MIN` минут. Сразу — `make ingest-news` или
   `POST /api/v1/admin/ingest/news`. Ручная новость — `POST /api/v1/admin/news`.

## Подключение внешних компонентов

| Что | Настройка | После изменения |
|---|---|---|
| ML-сервис команды ML | `INSIGHT_GENERATOR=remote`, `ML_SERVICE_URL`, `ML_SERVICE_TOKEN` | `POST /api/v1/admin/insights/regenerate` для нужных компаний |
| LLM организаторов | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_MAP` (алиас → ID), `LLM_PRICES` (₽ за 1M токенов) | проверка: `make smoke-live` |
| Модель эмбеддингов (выбирает ML) | `EMBEDDING_PROVIDER=local` + `EMBEDDING_MODEL`, или `remote` + `EMBEDDING_REMOTE_URL`; размерность 384 | `docker compose exec api python scripts/ingest.py analytics --reembed` |
| Публичный адрес S3 для ссылок на PDF | `S3_PUBLIC_ENDPOINT_URL` | — |
| Фронтенд | `CORS_ORIGINS` | — |

Секреты (`JWT_SECRET`, `SERVICE_TOKEN`, `ML_SERVICE_TOKEN`, `LLM_API_KEY`) — только в `.env` или в окружении.
С `APP_ENV=prod` и dev-значениями сервис не стартует.

## Мониторинг

| Что смотреть | Где |
|---|---|
| Жив ли сервис и его зависимости | `GET /health`, `GET /ready` |
| HTTP, LLM, бюджет (Prometheus) | `GET /metrics`: `http_requests_total`, `llm_requests_total`, `llm_cost_rub_total`, `llm_budget_remaining_rub`, `llm_budget_exhausted_total` |
| Качество и продукт | `GET /api/v1/admin/metrics` (`docs/METRICS.md`) |
| Расход LLM | `GET /api/v1/admin/llm-usage` |
| Источники новостей | `GET /api/v1/admin/sources/health` |
| Качество загрузки данных | `GET /api/v1/admin/data-quality` |
| Конкретный запрос | `X-Trace-Id` ответа → поиск в логах (`trace_id`); для инсайта — `GET /insights/{id}/trace` |

## Типовые ситуации

| Симптом | Причина и действие |
|---|---|
| Лента пуста сразу после старта | Генерация ещё идёт: `docker compose logs worker`. Если в логах `ml-stub ... not ready`, проверьте `docker compose ps ml-stub` |
| Инсайты не появляются, в `/admin/metrics` растёт `generation.rejected` | Агент возвращает невалидные инсайты: причины — в `generation.reject_reasons` и `core.insights_rejected`; передать ML-команде |
| Вопросы отвечают `error: ml-unavailable` | ML-сервис недоступен: проверить `ML_SERVICE_URL`, `docker compose logs ml-stub` |
| `429 budget-exhausted` в логах агента | Дневной бюджет LLM исчерпан (`/admin/llm-usage`). Сервис работает без LLM (`allow_llm=false`); бюджет обновится в 00:00 МСК. Поднимать `LLM_DAILY_BUDGET_RUB` — только в пределах лимита организаторов |
| Источник `down` в `/admin/sources/health` | После 3 неудачных опросов опрашивается резервный (`fallback_for`). Причина — в `last_error`; `robots_disallowed` не повторяется до правки каталога |
| PDF аналитики `failed` | Причина — в `analytics_documents.error`. Лимиты: `ANALYTICS_MAX_BYTES`, `ANALYTICS_MAX_PAGES`. Пустой результат OCR не публикуется |
| Поиск по аналитике не находит смысловые совпадения | `EMBEDDING_PROVIDER=fake` или модель недоступна: работает только полнотекстовая часть (`vector_rank=null`). Подключить модель (см. выше) и переиндексировать |
| 429 `rate-limited` у фронтенда | Поднять `RATE_LIMIT_PUBLIC_PER_MIN` или проверить, не зациклился ли клиент |
| Redis недоступен | Очередь стоит: новые новости и генерация ждут. Ранее созданные инсайты по-прежнему отдаются, rate limit пропускает запросы. Поднять `redis`, задачи продолжатся |

## Изменения схемы и данных

- **Миграции.** `make migrate` (Alembic). Integration-тест проверяет, что модели и схема не расходятся.
- **Миграция 0006** добавляет `scoring_features` и `counterparty_tags_any`. На существующей базе пересоберите
  контекст (`make ingest-companies`), иначе для старых записей используются полные признаки.
- **Синтетика** перегенерируется детерминированно: `make synthetic`.
- **Бэкапы** (`pg_dump` и `mc mirror` по расписанию) — в `docs/BACKLOG.md`; для демо-стенда не настроены.
