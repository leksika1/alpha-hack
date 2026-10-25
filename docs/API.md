# API бэкенда

Полная спецификация — [`docs/openapi.json`](openapi.json) (OpenAPI 3.1, `make openapi`); на работающем стенде —
Swagger `http://localhost:8000/docs`. Внутренние API для ML описаны в [`contracts/`](../contracts/README.md).
Гайд для фронтенда (экраны → эндпоинты, SSE) — [`INTEGRATION_FRONTEND.md`](INTEGRATION_FRONTEND.md).

## Общие правила

- **Префикс** `/api/v1`. Формат — JSON, время в ISO 8601 UTC.
- **Авторизация.** `POST /auth/login` выдаёт пару токенов: access (30 мин) и refresh (7 дней). Дальше заголовок
  `Authorization: Bearer <access>`; обновление — `POST /auth/refresh`. Данные компании доступны, только если она
  есть в `user_company_access`, иначе 403. Admin-эндпоинты требуют `platform_role=admin`.
- **Ошибки** — RFC 7807 `application/problem+json`: `type`, `title`, `status`, `detail`, `instance`, `trace_id`.
  `trace_id` совпадает с заголовком `X-Trace-Id` и ищется в логах.
- **Пагинация.** Лента — непрозрачным курсором `next_cursor`; уведомления — по `before_id`.
- **Лимиты.** Token bucket на вызывающего (`RATE_LIMIT_PUBLIC_PER_MIN`); при превышении — 429 с `Retry-After`.
- **SSE** (вопросы, уведомления) — `text/event-stream` с авторизацией заголовком, клиенту нужен `fetch` со
  стримингом. События описаны ниже.

## Публичное API

| Метод | Путь | Назначение |
|---|---|---|
| **Авторизация и профиль пользователя** | | |
| POST | `/auth/login` | Вход по учётке |
| POST | `/auth/refresh` | Обновить access-токен |
| GET | `/me` | Пользователь, его компании и роли |
| PUT | `/me/companies/{company_id}/role` | Сменить роль в компании; варианты инсайтов перегенерируются |
| GET | `/me/requests` | Мои заявки после инсайтов и их статус |
| **Компании** | | |
| GET | `/companies` | Компании пользователя |
| GET | `/companies/{id}/profile` | Что сервис знает о компании: признаки, источник каждого, полнота |
| PATCH | `/companies/{id}/profile` | Дополнить профиль (поставщики, покупатели, сырьё, валюты, планы); пересчёт и перегенерация |
| GET | `/companies/{id}/graph` | Группа, контрагенты, связи; гипотезы помечены и снабжены уверенностью |
| **Лента** | | |
| GET | `/market` | Ключевая ставка и официальные курсы ЦБ (доллар, евро, юань): значение, дата, предыдущее значение, изменение, источник (ADR-043) |
| GET | `/feed/summary` | Виджет на главной: непрочитанные, срочные, главный инсайт по каждой компании |
| GET | `/feed` | Лента компании: `role`, `filter` (all, urgent, risks, opportunities, saved, hidden), `period`, `limit`, `cursor`; в `all` — не больше `FEED_TOPIC_CAP` карточек одной макротемы, скрытые — в `more_in_topic` (ADR-042) |
| GET | `/digest` | Дайджест за день или неделю |
| **Инсайт** | | |
| GET | `/insights/{id}` | Полный инсайт (`contracts/insight.schema.json`); `via`, `position` — для метрик |
| GET | `/insights/{id}/trace` | На чём основан вывод: признаки, источники, шаги агента, вызовы инструментов и LLM, скоринг, проверка |
| POST | `/insights/{id}/state` | `open`, `hide`, `unhide`, `save`, `unsave`, `watch` |
| POST | `/insights/{id}/feedback` | Полезно или бесполезно + причина; ответ — что сделано с отзывом |
| POST | `/news/submit` | Прислать ссылку или текст новости для своей компании: `processed`, `duplicate`, `unavailable` («ресурс недоступен для просмотра»), `blocked`, `not_article` (ADR-038) |
| POST | `/insights/{id}/source-click` | Ссылка на первоисточник: новость или страница PDF (через API, 15 мин) |
| POST | `/insights/{id}/actions` | Менеджер, обратный звонок, заявка → мок-CRM |
| **Вопросы к аналитике** | | |
| POST | `/insights/{id}/ask` | Вопрос к инсайту (SSE) |
| POST | `/ask` | Вопрос по компании (SSE) |
| GET | `/insights/{id}/questions`, `/ask/history` | История вопросов |
| **Уведомления** | | |
| GET | `/notifications` | Список (`unread_only`, `before_id`) |
| POST | `/notifications/{id}/read`, `/notifications/read-all` | Отметить прочитанным |
| GET | `/notifications/stream` | SSE |
| **Справочники и первоисточники** | | |
| GET | `/bank-products` | Каталог продуктов банка |
| GET | `/news/{id}` | Новость: первоисточник события, флаги, теги |
| GET | `/analytics/documents` | Материалы аналитики банка |
| GET | `/analytics/documents/{id}/download` | Ссылка на PDF (15 минут), `#page=N` |
| GET | `/analytics/documents/{id}/file?token=` | Сам PDF по ссылке из `download` / `source-click`; доступ по токену в адресе (ADR-033) |
| POST | `/events` | События интерфейса для воронки (`widget_shown`, `widget_click`, `card_impression`, …) |
| **Администрирование** (`admin`) | | |
| GET | `/admin/metrics` | North star и метрики этапов CJM, генерация, ежедневные снимки (`docs/METRICS.md`) |
| GET | `/admin/llm-usage` | Бюджет LLM (и доля вопросов: `chat_limit_rub`, `chat_spent_rub`) и расход по дням, моделям, целям |
| GET | `/admin/data-quality` | Отчёт качества последней загрузки компаний |
| GET | `/admin/sources/health` | Здоровье источников новостей |
| GET | `/admin/sources` | Все источники: откуда (`catalog` / `admin`), включён ли, здоровье, новостей за сутки |
| POST | `/admin/sources/discover` | Найти ленты сайта по названию (справочник) или по ссылке на сайт, каждая проверена |
| POST | `/admin/sources/check` | Проверить ленту без сохранения: доступность, robots.txt, разбор, последние новости |
| POST | `/admin/sources` | Добавить RSS/Atom-источник (после проверки), опрос сразу в очереди |
| PATCH | `/admin/sources/{id}` | Включить/выключить; у добавленных админом — имя, приоритет, резерв, темы |
| DELETE | `/admin/sources/{id}` | Удалить добавленный админом источник, пока по нему нет новостей |
| POST | `/admin/ingest/{kind}` | Поставить в очередь `news` или `analytics` |
| POST | `/admin/news` | Ручная новость через общий конвейер |
| POST | `/admin/news/cleanup?days=14` | Привести накопленные новости к текущим правилам: перетегировать, снять онлайн-трансляции, склеить события одного решения ЦБ, перегенерировать их инсайты (ADR-040) |
| POST | `/admin/analytics?file_name=` | Загрузить PDF аналитики (тело — файл, `application/pdf`); обработка в очереди |
| GET | `/admin/analytics` | Все материалы аналитики со статусом обработки (`processing`, `ready`, `failed`) и ошибкой |
| POST | `/admin/insights/regenerate` | Перегенерировать инсайты компаний |
| GET | `/admin/generation` | Монитор генерации: очередь, кандидаты по статусам, запуски агента за сутки (p50/p95), зависшие запуски, последние ошибки, доступность ML |
| POST | `/admin/market/refresh` | Обновить ставку и курсы ЦБ сейчас; по каждому индикатору — число значений или ошибка |
| GET | `/admin/alerts` | Активные проблемы: источники, бюджет LLM, ошибки LLM, отклонения, зависшие запуски, ML (ADR-034, ADR-042) |
| POST | `/admin/generation/retry` | Закрыть зависшие запуски, вернуть упавших (и по желанию отклонённых) кандидатов в очередь |
| PATCH | `/admin/crm-requests/{id}` | Сменить статус заявки мок-CRM (пользователь получит уведомление) |

Служебные эндпоинты (без префикса):
- `GET /health` — процесс жив;
- `GET /ready` — Postgres, Redis и бакеты S3 доступны;
- `GET /metrics` — Prometheus.

## SSE-события

**Вопрос** (`/insights/{id}/ask`, `/ask`):

| Событие | Данные |
|---|---|
| `run` | `run_id` (трассировка), `question_id` |
| `step` | шаг агента `{type, name}` — для индикатора «ищет в аналитике…» |
| `token` | `{delta}` — часть ответа |
| `source` | источник (`insight.schema.json#/$defs/source`) |
| `done` | `ChatFinal`: `answer`, `sources`, `statements` (`kind` — fact, bank_analytics, model_inference, hypothesis), `disclaimers` — после проверки бэкендом |
| `error` | problem+json: `ml-unavailable` (503), `agent-timeout` (504), `invalid-answer` / `agent-no-answer` (502) |

**Уведомления** (`/notifications/stream`):

| Событие | Данные |
|---|---|
| `ready` | `unread`, `last_id` |
| `notification` | уведомление (`id:` в заголовке события): `kind` — urgent_insight, digest или request_update; `title`, `body`, `company_id`, `insight_id`, `payload` |
| `: ping` | комментарий раз в 15 с |

Соединение закрывается через `max_s` (по умолчанию 300 с). Клиент переподключается с заголовком `Last-Event-ID`.

## Внутренние API (для ML-агента)

| Префикс | Контракт | Доступ |
|---|---|---|
| `/internal/tools/*` | [`contracts/tools-api.openapi.yaml`](../contracts/tools-api.openapi.yaml) | run-токен + `X-Run-Id`; только компания запуска; аудит |
| `/internal/llm/*` | [`contracts/llm-gateway.openapi.yaml`](../contracts/llm-gateway.openapi.yaml) | run-токен или `ML_SERVICE_TOKEN`; белый список, бюджет, маскирование |
| ML-сервис: `/v1/insights/generate`, `/v1/chat`, `/v1/score` | [`contracts/ml-service.openapi.yaml`](../contracts/ml-service.openapi.yaml) | бэкенд → ML, `ML_SERVICE_TOKEN` |
