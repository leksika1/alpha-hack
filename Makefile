# Canonical command interface (ADR-014). Every target is 1-2 plain commands that can be run by hand.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE      := docker compose
COMPOSE_E2E  := docker compose -p alfa-e2e -f docker-compose.yml -f docker-compose.test.yml --profile test
RUN          := uv run
APP_EXEC     := $(COMPOSE) exec api

.PHONY: help up down logs ps migrate seed synthetic ingest-companies ingest-news ingest-analytics \
        install lint format typecheck test integration e2e check openapi notices smoke-live clean-e2e

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps from uv.lock (local dev)
	uv sync --frozen

up: ## Build and start the demo stack
	$(COMPOSE) up -d --build

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

logs: ## Follow logs
	$(COMPOSE) logs -f --tail=200

ps: ## Service status
	$(COMPOSE) ps

migrate: ## alembic upgrade head (inside compose)
	$(COMPOSE) run --rm migrate

seed: ## Reference data, demo users, pre-generated insights
	$(COMPOSE) run --rm seed

synthetic: ## Regenerate data/synthetic (deterministic)
	$(RUN) python scripts/make_synthetic.py

ingest-companies: ## Load companies CSV (DATA_DIR) -> staging -> core -> graph -> context
	$(APP_EXEC) python scripts/ingest.py companies

ingest-news: ## Poll news sources once
	$(APP_EXEC) python scripts/ingest.py news

ingest-analytics: ## Load bank analytics PDFs (OCR -> chunks -> embeddings)
	$(APP_EXEC) python scripts/ingest.py analytics

format: ## Auto-format
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

lint: ## ruff lint + format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

typecheck: ## mypy (strict for app/domain, app/services)
	$(RUN) mypy

test: ## Unit tests
	$(RUN) pytest -m "not integration and not e2e and not live" -q

integration: ## Integration tests (testcontainers: Postgres+pgvector, Redis, MinIO)
	$(RUN) pytest -m integration -q

check: lint typecheck test integration ## lint + typecheck + unit + integration

e2e: ## Full stack in `test` profile + tests/e2e
	$(COMPOSE_E2E) up --build --abort-on-container-exit --exit-code-from e2e-runner e2e-runner; \
	status=$$?; [ $$status -eq 0 ] || $(COMPOSE_E2E) logs --tail=300; \
	$(COMPOSE_E2E) down -v --remove-orphans; exit $$status

clean-e2e: ## Remove e2e stack and volumes
	$(COMPOSE_E2E) down -v --remove-orphans

openapi: ## Export docs/openapi.json
	$(RUN) python scripts/export_openapi.py

notices: ## Generate THIRD_PARTY_NOTICES.md from uv.lock
	$(RUN) python scripts/gen_notices.py

smoke-live: ## One real news source + one real LLM call (needs LLM_* env; excluded from CI)
	$(RUN) pytest -m live -q
