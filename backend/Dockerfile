# syntax=docker/dockerfile:1.7
# One image for api / worker / scheduler / migrate / seed / ml-stub (different commands).
# Build context: repository root (needs backend/ and the shared contracts/):
#   docker build -f backend/Dockerfile .

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM base AS builder
ARG UV_VERSION=0.13.5
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY backend/app ./app
COPY backend/ml_stub ./ml_stub
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS runtime
# tesseract + Russian language pack: OCR of scanned bank analytics (ADR-005); curl for healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder /app/.venv /app/.venv
COPY backend/app ./app
COPY backend/ml_stub ./ml_stub
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/pyproject.toml backend/README.md ./
COPY backend/scripts ./scripts
COPY backend/data/synthetic ./data/synthetic
COPY backend/data/reference ./data/reference
COPY contracts ./contracts
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Test image: runtime + dev dependencies + tests (used by the e2e runner in the `test` profile)
FROM runtime AS test
USER root
ARG UV_VERSION=0.13.5
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY backend/tests ./tests
USER appuser
CMD ["pytest", "tests/e2e", "-m", "e2e", "-q"]
