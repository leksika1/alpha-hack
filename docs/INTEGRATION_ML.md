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
