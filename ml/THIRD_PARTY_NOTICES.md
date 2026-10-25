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
| fastembed (+ onnxruntime, tokenizers, huggingface-hub) | Apache-2.0 / MIT | эмбеддинги ml-ranker@2 на CPU (ADR-ML-011) |
| scikit-learn (только офлайн-обучение `eval/train_ranker2.py`, ставится отдельно, не в образе) | BSD-3 | обучение ml-ranker@2 |
| pandas, pyarrow (группа `analysis`) | BSD-3 / Apache-2.0 | EDA и офлайн-оценка |

## Модели

| Модель | Назначение | Условия |
|---|---|---|
| Gemma 4 26B A4B, Qwen3.6 35B A3B, GLM-5.3-Flash (+ остальные из белого списка) | планировщик, аналитик, критик, чат — только через LLM Gateway бэкенда | провайдер организаторов, лимит 500 ₽/день |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (fastembed, ONNX) | эмбеддинги RAG бэкенда (`EMBEDDING_PROVIDER=local`), ADR-ML-006; семантические признаки ml-ranker@2, ADR-ML-011 | Apache-2.0 |
| `jinaai/jina-reranker-v2-base-multilingual` | только абляция ml-ranker@2 (выключен, в образ не входит) | CC-BY-NC-4.0 — поэтому и не в продукте |
| `rules@1`, `ml-ranker@1`, `ml-ranker@2` (веса `alfa_ml/models/ranker2.json`) | детерминированный аналитик и ранжировщик (код команды) | отдельный LICENSE проекта не задан |

## Источники идей

Кластеризация событий и документирование критериев — по мотивам открытых репозиториев финала PROD-2026
(BrandRadar, `github.com/Central-University-IT-prod`); код не копировался.
