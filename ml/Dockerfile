# syntax=docker/dockerfile:1.7
# ML service (insight agent, chat, ranker). Build context: repository root (needs ml/ and the shared contracts/):
#   docker build -f ml/Dockerfile -t alfa-future-ml .

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    CONTRACTS_DIR=/app/contracts \
    FASTEMBED_CACHE_PATH=/app/models \
    HF_HUB_OFFLINE=1
WORKDIR /app

FROM base AS builder
ARG UV_VERSION=0.13.5
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY ml/pyproject.toml ml/uv.lock ml/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY ml/alfa_ml ./alfa_ml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# ml-ranker@2 semantic layer (ADR-ML-011): bake the ONNX embedder (~120 MB) into the image, so the running
# service never downloads from Hugging Face. No network at build or SKIP_MODELS=1 -> image without the model,
# the service logs semantic_unavailable and keeps ml-ranker@1.
ARG SKIP_MODELS=0
ENV FASTEMBED_CACHE_PATH=/app/models
RUN mkdir -p /app/models && if [ "$SKIP_MODELS" != "1" ]; then \
      HF_HUB_OFFLINE=0 python -c "from alfa_ml.semantic import FastembedBackend as B; B().embed(['тест'])" \
        || echo "WARNING: embedding model not baked, ml-ranker@1 fallback"; \
    fi

FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10002 mluser
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder --chown=10002:10002 /app/models /app/models
COPY ml/alfa_ml ./alfa_ml
COPY contracts ./contracts
USER mluser
EXPOSE 8200
HEALTHCHECK --interval=10s --timeout=3s --retries=10 CMD curl -fsS http://localhost:8200/health || exit 1
CMD ["uvicorn", "alfa_ml.main:app", "--host", "0.0.0.0", "--port", "8200"]
