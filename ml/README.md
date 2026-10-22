# ml — ML-сервис (зона команды ML)

Здесь живёт ML-сервис: агент генерации инсайтов, ответы на вопросы, (опционально) ранжирование.
Бэкенд эту папку не меняет.

## Что реализовать

Сервис по контракту [`../contracts/ml-service.openapi.yaml`](../contracts/ml-service.openapi.yaml):
`GET /health`, `POST /v1/insights/generate`, `POST /v1/chat` (SSE), опционально `POST /v1/score`.
Результат генерации — инсайт по [`../contracts/insight.schema.json`](../contracts/insight.schema.json).

## Что даёт бэкенд

- Tools API (`/internal/tools/*`) — [`../contracts/tools-api.openapi.yaml`](../contracts/tools-api.openapi.yaml):
  контекст компании, группа, контрагенты, новости и события, аналитика банка (RAG), продукты, `log_step` для трейса.
- LLM Gateway (`/internal/llm/*`) — [`../contracts/llm-gateway.openapi.yaml`](../contracts/llm-gateway.openapi.yaml):
  единственный доступ к LLM, только 6 моделей из белого списка.
- Словарь признаков компании — [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md); гайд — [`../docs/INTEGRATION_ML.md`](../docs/INTEGRATION_ML.md).

## Подключение к общему стеку

Пока ML-сервиса нет, работает детерминированная заглушка `backend/ml_stub` с тем же контрактом.
Чтобы подключить свой сервис: добавить его в `docker-compose.yml` и выставить `ML_SERVICE_URL=http://<сервис>:<порт>`,
`INSIGHT_GENERATOR=remote`.
