# Гайд для ML-команды

Цель: ваш агент получает задачу от бэкенда, собирает данные через наши инструменты, вызывает LLM через наш Gateway
и возвращает инсайт по контракту. Всё, что ниже, описано формально в `contracts/` (OpenAPI + JSON Schema + примеры).

## Что вы реализуете

Сервис по `contracts/ml-service.openapi.yaml`:

| Эндпоинт | Обязателен | Суть |
|---|---|---|
| `GET /health` | да | liveness |
| `POST /v1/insights/generate` | да | инсайт для (company_id, event_id, role) → `insight.schema.json` или `status: skipped` |
| `POST /v1/chat` | да | ответ на вопрос пользователя, SSE (`step`/`token`/`source`/`done`/`error`) |
| `POST /v1/score` | нет | батч-скоринг кандидатов (иначе работает наш baseline) |

Подключение: `ML_SERVICE_URL=http://<ваш-сервис>:<порт>`, `INSIGHT_GENERATOR=remote`. Пока вас нет — работает `ml-stub` с тем же контрактом.

## Что вы получаете от нас

- **Tools API** (`contracts/tools-api.openapi.yaml`): контекст компании (признаки + источник + полнота), группа, контрагенты с долями и отраслями,
  гибридный поиск новостей, событие со всеми новостями, RAG по аналитике банка (цитата + страница + `chunk_id`), каталог продуктов,
  рыночные данные (если есть), `log_step` для трейса.
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
