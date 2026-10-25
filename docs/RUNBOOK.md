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

## Деплой (Dokploy)

Схема (ADR-032): каждая часть — отдельный Compose-сервис Dokploy из своего файла; корневой `docker-compose.yml`
в Dokploy не используется — это весь стек для локального запуска и CI. Два окружения: prod — ветка `main`,
dev — ветка `dev`; в каждом свои сервисы.

| Сервис | Compose Path | Что поднимает | Domains |
|---|---|---|---|
| **Backend** | `./backend/docker-compose.yml` | api, worker, scheduler, ml-stub, migrate, seed, Postgres, Redis, MinIO | необязательно: домен API → `api`, порт `8000` |
| **Frontend** | `./frontend/docker-compose.yml` | nginx с фронтендом, `/api` → бэкенд | домен → `frontend`, порт `80` |
| **ML** | `./ml/docker-compose.yml` | ML-сервис команды ML (агент, чат, ранжирование) | не нужен |

```
Интернет → Traefik → frontend ──dokploy-network──▶ api (BACKEND_ALIAS) ◀──▶ ML (ML_ALIAS)
                                                     └─ сеть проекта Backend: Postgres, Redis, MinIO
```

Compose-файлы частей по умолчанию настроены на Dokploy (сеть `dokploy-network`), как в VTB-Hack: в Environment
только алиас и секреты. Секреты — `openssl rand -hex 32`, у prod и dev разные.

**Backend → Environment**

```
BACKEND_ALIAS=alfa-backend-prod       # в dev: alfa-backend-dev
ML_ALIAS=alfa-ml-prod                 # в dev: alfa-ml-dev; бэкенд зовёт ML по нему
APP_ENV=prod                          # с dev-секретами из .env.example сервис не стартует
JWT_SECRET=<openssl rand -hex 32>
SERVICE_TOKEN=<openssl rand -hex 32>
ML_SERVICE_TOKEN=<openssl rand -hex 32>   # такой же — в сервисе ML
POSTGRES_PASSWORD=<openssl rand -hex 32>
MINIO_ROOT_PASSWORD=<openssl rand -hex 32>
LLM_API_KEY=<ключ команды>           # адрес провайдера и ID моделей заданы по умолчанию; без ключа — правила rules@1
```

**Frontend → Environment**

```
BACKEND_ALIAS=alfa-backend-prod       # то же, что у Backend этого окружения
```

**ML → Environment**

```
ML_ALIAS=alfa-ml-prod                 # то же, что у Backend этого окружения
ML_SERVICE_TOKEN=<как у Backend>
```

Адреса Tools API и LLM Gateway бэкенд передаёт ML в каждом запросе (`http://<BACKEND_ALIAS>:8000`), отдельно их
задавать не нужно. Настройки агента (`ML_LLM_*`, `ML_MODEL_*`) — необязательные, см. `ml/README.md`.

Порядок первого запуска: ML и Backend (миграции и seed), затем Frontend; до старта ML генерация ждёт его. Проверка: `https://<домен>/api/v1/...`
через фронтенд или `/ready` на домене API.

**Автодеплой** — задание CI `deploy` после зелёных тестов бэкенда, ML и сборки фронтенда на push в `main`/`dev`:
1. Dokploy → каждый сервис: ветка своего окружения, **Auto Deploy включён** — без него Dokploy отклоняет вебхук
   (отвечает 301); Deployments → Webhook URL.
2. Источник сервиса — провайдер **Git** (URL репозитория), а не GitHub App: через приложение GitHub Dokploy
   деплоит сам на каждый push, не дожидаясь тестов, и вместе с вебхуком CI деплой будет двойным.
3. GitHub → Settings → Environments → `prod` и `dev` → секреты `DOKPLOY_WEBHOOK_BACKEND`, `DOKPLOY_WEBHOOK_ML`,
   `DOKPLOY_WEBHOOK_FRONTEND` (вебхуки сервисов этого окружения).
4. Часть без своего compose-файла не деплоится; для существующей части без секрета задание красное; успех — только
   HTTP 200 (301 — у сервиса выключен Auto Deploy или другая ветка).

## Подключение внешних компонентов

| Что | Настройка | После изменения |
|---|---|---|
| ML-сервис команды ML | `INSIGHT_GENERATOR=remote`, `ML_SERVICE_URL`, `ML_SERVICE_TOKEN` | `POST /api/v1/admin/insights/regenerate` для нужных компаний |
| LLM организаторов | `LLM_API_KEY` (ключ команды). Адрес `https://changellenge.up.railway.app/v1` и ID моделей (`google/gemma-4-26b-a4b-it`, `z-ai/glm-5.3-flash`, `qwen/qwen3.6-35b-a3b`, `deepseek/deepseek-v4.1-flash`, `qwen/qwen3-vl-30b-a3b-instruct`, `moonshotai/kimi-k2.6`) заданы по умолчанию; `LLM_BASE_URL`, `LLM_MODEL_MAP` — только для замены; `LLM_PRICES` (₽ за 1M токенов) — когда будут тарифы | проверка: `make smoke-live` |
| Модель эмбеддингов (выбирает ML) | `EMBEDDING_PROVIDER=local` + `EMBEDDING_MODEL`, или `remote` + `EMBEDDING_REMOTE_URL`; размерность 384 | `docker compose exec api python scripts/ingest.py analytics --reembed` |
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
| Генерация инсайтов | `GET /api/v1/admin/generation`: очередь, кандидаты, запуски за сутки, зависшие, ошибки, ML |
| Активные проблемы | `GET /api/v1/admin/alerts`; раз в 5 минут те же условия уходят в Telegram или в лог (ADR-034) |

**Алерты в Telegram:** создать бота у @BotFather, добавить его в чат команды, узнать `chat_id`
(`https://api.telegram.org/bot<токен>/getUpdates` после сообщения в чат). В Environment бэкенда:
`ALERTS_PROVIDER=telegram`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERTS_LABEL=prod` (или `dev`).

## Типовые ситуации

| Симптом | Причина и действие |
|---|---|
| Лента пуста сразу после старта | Генерация ещё идёт: `GET /api/v1/admin/generation` (очередь, кандидаты `pending`, доступность ML) или `docker compose logs worker`. `ml_service: unavailable` — проверить сервис `ml` |
| В `/admin/generation` есть `stuck_runs` или растёт `candidates.failed` | Воркер упал посреди генерации или ML не отвечал: `POST /api/v1/admin/generation/retry` закроет зависшие запуски и вернёт упавших кандидатов в очередь (`include_rejected` — и отклонённых) |
| Инсайты не появляются, в `/admin/metrics` растёт `generation.rejected` | Агент возвращает невалидные инсайты: причины — в `generation.reject_reasons` и `core.insights_rejected`; передать ML-команде |
| Вопросы отвечают `error: ml-unavailable` | ML-сервис недоступен: проверить `ML_SERVICE_URL`, `docker compose logs ml-stub` |
| В трассе инсайтов нет шагов `llm_call`, в `/admin/llm-usage` строки `status=error` | Причина — поле `error`: `llm-not-configured` — в Environment бэкенда нет `LLM_API_KEY`; `llm-upstream-error` / `llm-unavailable` / `llm-timeout` — провайдер отвечает ошибкой или висит (проверить `GET <LLM_BASE_URL>/models` с ключом); `cancelled` — агент не дождался ответа (таймаут ML 25 с); `abandoned` — резерв без результата, закрыт автоматически. Пока LLM недоступен, агент пишет инсайты по правилам |
| `429 budget-exhausted` в логах агента | Дневной бюджет LLM исчерпан (`/admin/llm-usage`); у вопросов к аналитике своя доля — `LLM_CHAT_BUDGET_SHARE`, 30 % дня (`chat_spent_rub` / `chat_limit_rub`, ADR-035). Сервис работает без LLM (`allow_llm=false`); бюджет обновится в 00:00 МСК. Поднимать `LLM_DAILY_BUDGET_RUB` — только в пределах лимита организаторов |
| Один источник `down` (часто зарубежный) | Нормально: источник пропускается, алерта нет (ADR-039). Если недоступна половина и больше — алерт `sources-down`: проверить сеть и DNS сервера |
| Источник `down` в `/admin/sources/health` | После 3 неудачных опросов опрашивается резервный (`fallback_for`). Причина — в `last_error`; `robots_disallowed` не повторяется до правки каталога |
| PDF аналитики `failed` | Причина — в `GET /api/v1/admin/analytics` (поле `error`). Загрузить файл заново — `POST /api/v1/admin/analytics?file_name=…` (тело — PDF). Лимиты: `ANALYTICS_MAX_BYTES`, `ANALYTICS_MAX_PAGES`. Пустой результат OCR не публикуется |
| Поиск по аналитике не находит смысловые совпадения | `EMBEDDING_PROVIDER=fake` или модель недоступна: работает только полнотекстовая часть (`vector_rank=null`). Подключить модель (см. выше) и переиндексировать |
| 429 `rate-limited` у фронтенда | Поднять `RATE_LIMIT_PUBLIC_PER_MIN` или проверить, не зациклился ли клиент |
| Redis недоступен | Очередь стоит: новые новости и генерация ждут. Ранее созданные инсайты по-прежнему отдаются, rate limit пропускает запросы. Поднять `redis`, задачи продолжатся |

## Изменения схемы и данных

- **Миграции.** `make migrate` (Alembic). Integration-тест проверяет, что модели и схема не расходятся.
- **Миграция 0006** добавляет `scoring_features` и `counterparty_tags_any`. На существующей базе пересоберите
  контекст (`make ingest-companies`), иначе для старых записей используются полные признаки.
- **Синтетика** перегенерируется детерминированно: `make synthetic`.
- **Бэкапы** (`pg_dump` и `mc mirror` по расписанию) — в `docs/BACKLOG.md`; для демо-стенда не настроены.
