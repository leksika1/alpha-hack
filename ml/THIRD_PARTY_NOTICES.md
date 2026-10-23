# THIRD_PARTY_NOTICES — ML-сервис (`ml/`)

Дополнение к корневому `THIRD_PARTY_NOTICES.md` (его генерирует бэкенд). Версии пакетов — `ml/uv.lock`.

## Python-пакеты

| Пакет | Лицензия | Назначение |
|---|---|---|
| fastapi, starlette | MIT / BSD-3 | HTTP-сервис по контракту |
| uvicorn[standard] | BSD-3 | ASGI-сервер |
| httpx | BSD-3 | клиенты Tools API и LLM Gateway |
| pydantic | MIT | разбор ответов LLM, конфиг |
| jsonschema[format-nongpl] | MIT | самопроверка по `insight.schema.json` |
| structlog | MIT / Apache-2.0 | JSON-логи с именованными событиями |
| pytest, pytest-asyncio, ruff, mypy (dev) | MIT | тесты и проверки |
| pandas, pyarrow (группа `analysis`) | BSD-3 / Apache-2.0 | EDA и офлайн-оценка |

## Модели

| Модель | Назначение | Условия |
|---|---|---|
| Gemma 4 26B A4B, Qwen3.6 35B A3B, GLM-5.3-Flash (+ остальные из белого списка) | планировщик, аналитик, критик, чат — только через LLM Gateway бэкенда | провайдер организаторов, лимит 500 ₽/день |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (fastembed, ONNX) | эмбеддинги RAG бэкенда (`EMBEDDING_PROVIDER=local`), ADR-ML-006 | Apache-2.0 |
| `rules@1`, `ml-ranker@1` | детерминированный аналитик и ранжировщик (код этого репозитория) | MIT |

## Источники идей

Кластеризация событий и документирование критериев — по мотивам открытых репозиториев финала PROD-2026
(BrandRadar, `github.com/Central-University-IT-prod`); код не копировался.
