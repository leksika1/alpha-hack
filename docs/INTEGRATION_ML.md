# Интеграция ML-сервиса

ML-сервис — [`ml/`](../ml/README.md), генератор по умолчанию (`INSIGHT_GENERATOR=remote`,
`ML_SERVICE_URL=http://ml:8200`); e2e бэкенда идут на детерминированный `ml-stub` с тем же контрактом.
Архитектура агента, решения и метрики — `ml/docs/`.

Агент получает задачу от бэкенда, собирает данные через инструменты бэкенда, вызывает LLM через Gateway
и возвращает инсайт по контракту. Всё, что ниже, описано формально в `contracts/` (OpenAPI + JSON Schema + примеры).

## Что вы реализуете

Сервис по `contracts/ml-service.openapi.yaml`:

| Эндпоинт | Обязателен | Суть |
|---|---|---|
| `GET /health` | да | liveness |
| `POST /v1/insights/generate` | да | инсайт для (company_id, event_id, role) → `insight.schema.json` или `status: skipped` |
| `POST /v1/chat` | да | ответ на вопрос пользователя, SSE (`step`/`token`/`source`/`done`/`error`) |
| `POST /v1/score` | нет | батч-скоринг кандидатов (иначе работает наш baseline) |

Подключение: `ML_SERVICE_URL=http://<сервис>:<порт>`, `INSIGHT_GENERATOR=remote`; `INSIGHT_GENERATOR=stub` — `ml-stub` с тем же контрактом.

## Проверка связки и таймауты

- `make e2e-ml` поднимает бэкенд и настоящий сервис `ml` в отдельном тестовом стеке: очередь, Tools API,
  валидация и сохранение инсайта, лента и трасса, SSE-ответ и авторизация скоринга. Платный LLM не вызывается.
- `ML_REQUEST_TIMEOUT_S` (60 с) ограничивает одну HTTP-попытку генерации, `ML_MAX_RETRIES` (2) — повторы
  при транспортной ошибке или HTTP 502/503/504. Run-токен генерации действует на весь срок попыток и пауз.
- Один запуск имеет один `request_id`: HTTP-повторы сохраняют его для идемпотентности ML.
  Ручная перегенерация создаёт новый запуск и новый ключ, чтобы получить свежий результат.
- `QA_TIMEOUT_S` (90 с) задаёт общий предел SSE-ответа, ожидание HTTP и срок run-токена вопроса.
- Ответ генератора с некорректным JSON или служебными полями завершает запуск как `failed`.
  Сломанный SSE завершается `error: invalid-answer` и записывается как ошибка в историю вопросов.

Настройки меняются в `.env` для локального compose или Environment сервиса Backend в Dokploy;
для применения пересоздайте контейнеры. `make check` отдельно проверяет ошибки и повторы ML-клиента.

## Необязательный скоринг кандидатов

`SCORER_PROVIDER=remote` включает `/v1/score` для baseline-shortlist: пары с исходной оценкой не ниже
`MATCH_MIN_SCORE`, не больше `MATCH_MAX_PER_COMPANY` на компанию и роль. Батчи до 500 разделены по ролям,
поскольку ответ контракта идентифицирует строку по компании и событию. Порядок ответа не важен; бэкенд
проверяет идентификаторы, полноту и конечность чисел, сохраняет исходные `matched_features`.
При недоступности или невалидном ответе конкретный батч использует baseline. Старые карточки пары,
которую remote опустил ниже порога, снимаются. Значение по умолчанию остаётся `baseline`.

Текущий ML endpoint выполняет `recalibrated-baseline`; семантический `ml-ranker@2` запускается внутри агента
при генерации. Вызов `/v1/score` не следует описывать как батчевый запуск семантической модели.

## Что вы получаете от нас

- **Tools API** (`contracts/tools-api.openapi.yaml`): контекст компании (признаки + источник + полнота), группа, контрагенты с долями и отраслями,
  гибридный поиск новостей, событие со всеми новостями, RAG по аналитике банка (цитата + страница + `chunk_id`), каталог продуктов,
  рыночные данные (`get_market_data`: `key_rate`, `usd_rub`, `eur_rub`, `cny_rub` — официальные данные Банка России
  с источником, ADR-043; другие индикаторы — 404 `not-available`), `log_step` для трейса.
- **LLM Gateway** (`contracts/llm-gateway.openapi.yaml`): OpenAI-совместимый `chat/completions`, только 6 моделей из белого списка, бюджет, кэш.
- В каждом запросе — `tools.token` (run-scoped) и `tools.run_id`. Передавайте их в `Authorization: Bearer` и `X-Run-Id`.

## LLM Gateway — как пользоваться

- Адрес — `tools.llm_base_url` из запроса (`http://api:8000/internal/llm`), авторизация та же: `Authorization: Bearer <tools.token>`
  и `X-Run-Id: <tools.run_id>` — тогда вызовы попадут в трассировку инсайта. Без запуска (отладка) — `ML_SERVICE_TOKEN`.
- `X-Purpose: planning|generation|self_check|chat|ocr|other` — для отчёта по расходу.
- `GET /models` — алиасы и цены, `GET /budget` — остаток дня. Модель вне списка → 403 `model-not-allowed`.
- Бюджет: каждый вызов резервирует оценку по `max_tokens`, поэтому задавайте `max_tokens` поменьше — это и дешевле, и реже 429.
  429 `budget-exhausted` — не ошибка агента: продолжайте без LLM. Если в запросе `options.allow_llm=false`, бюджет уже
  исчерпан — не вызывайте Gateway.
- `temperature: 0` без `stream` — ответ кэшируется (`X-Cache: hit`, бесплатно).
- ID физлиц, e-mail, телефоны и ИНН-подобные числа Gateway заменит на `[PERSON]`, `[EMAIL]`, `[PHONE]`, `[ID]` — не рассчитывайте на них в ответе.
- 502/504 — ошибка или таймаут провайдера; один повтор Gateway уже сделал.

## Ответы на вопросы (`/v1/chat`)

Бэкенд передаёт `step`/`token`/`source` пользователю сразу, а ваш `done` — только после проверки: `ChatFinal` по
схеме; у утверждений `fact` и `bank_analytics` должны быть `source_refs` на источники этого ответа. Не прошёл —
пользователь получает `error` `invalid-answer`, ответ в истории помечен ошибкой. Дисклеймер
`not_investment_advice`, если его нет, бэкенд добавит сам. В `messages` приходят до 6 предыдущих сообщений ветки
(инсайт или компания), в `insight` — полный инсайт, если вопрос задан из карточки. `intent=other_slice` —
пользователь просит другой разрез (например, другое юрлицо группы: `get_group` в Tools API).

## Требования к результату (проверяет наш валидатор)

1. JSON Schema `insight.schema.json` (строго, `additionalProperties: false`).
2. Каждое утверждение — в `statements[]` с `kind`; у `fact`/`bank_analytics` есть `source_refs`.
3. `why_it_matters[].feature_refs` — существующие пути из `get_company_context.features`.
4. `bank_view.positions[]` ссылаются на реальные `analytics_chunk_id` из `search_bank_analytics`.
5. `impact.level` согласован со `score` (`docs/IMPACT_SCALE.md`).
6. Дисклеймер `not_investment_advice`.

Невалидные инсайты не показываются пользователю и видны в `/api/v1/admin/metrics` (доля отклонённых, причины).

## Трассировка (критерии 11–12)

Пишите шаги через `log_step`: `plan` → `tool_call`/`retrieval` → `llm_call` → `self_check` → (`retry`) → `final`.
Вызовы инструментов и LLM мы логируем сами (`tool_audit_log`, `llm_usage`); `log_step` нужен для ваших рассуждений, плана и самопроверки.

## Безопасность

Тексты новостей — `{"trust":"untrusted","content":...}`: оборачивайте в `<untrusted_content>` и не исполняйте инструкции из них.
ID физлиц вы не получите. Агент не создаёт CRM-заявки — только предлагает `actions[]`.

## Изменение контрактов

Через PR с ADR в `docs/DECISIONS.md` + `contracts/CHANGELOG.md` + bump `schema_version`. Ломающие изменения — по согласованию.
