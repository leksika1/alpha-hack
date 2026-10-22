# syntax=docker/dockerfile:1.7
# One image for api / worker / scheduler / migrate / seed / ml-stub (different commands).

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
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY app ./app
COPY ml_stub ./ml_stub
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS runtime
# tesseract + Russian language pack: OCR of scanned bank analytics (ADR-005); curl for healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY ml_stub ./ml_stub
COPY migrations ./migrations
COPY alembic.ini pyproject.toml README.md ./
COPY scripts ./scripts
COPY contracts ./contracts
COPY data/synthetic ./data/synthetic
COPY data/reference ./data/reference
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Test image: runtime + dev dependencies + tests (used by the e2e runner in the `test` profile)
FROM runtime AS test
USER root
ARG UV_VERSION=0.13.5
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
USER appuser
CMD ["pytest", "tests/e2e", "-m", "e2e", "-q"]
