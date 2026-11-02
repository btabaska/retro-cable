# Retro Cable task runner. Targets are listed in CLAUDE.md; keep them working.
.DEFAULT_GOAL := help
SHELL := bash
.SHELLFLAGS := -euo pipefail -c

UV := uv run --project server
COMPOSE_TEST := docker compose -f ops/compose.yml -f ops/compose.test.yml
PY_PATHS := src tests ../tools/fixtures ../ops/tests
RC_DB ?= $(CURDIR)/data/retro-cable.db

.PHONY: help bootstrap fixtures dev lint lint-server lint-web test test-server test-web \
	test-tools test-ops e2e e2e-stack api api-check stack-up stack-down \
	catalog-check catalog-schema ingest inventory demo demo-db

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

bootstrap: ## Install toolchains and dependencies (needs uv, node, ffmpeg)
	@command -v uv >/dev/null || { echo "install uv: https://docs.astral.sh/uv/"; exit 1; }
	@command -v npm >/dev/null || { echo "install Node.js 24+"; exit 1; }
	@command -v ffmpeg >/dev/null || echo "warning: ffmpeg missing; make fixtures needs it"
	cd server && uv sync
	cd web && npm ci && npx playwright install chromium

fixtures: ## Generate fake media into .fixtures/
	$(UV) python tools/fixtures/generate.py --out .fixtures

dev: fixtures ## Run the API and web dev server on fixtures
	@trap 'kill 0' EXIT; \
	(cd server && uv run uvicorn retro_cable.app:app --reload --host 127.0.0.1 --port 8000 --no-access-log) & \
	(cd web && npm run dev -- --host 127.0.0.1) & \
	wait

DEMO_DB := $(CURDIR)/data/demo.db

demo-db: ## Demo feeds in .fixtures/_demo and a demo database (data/demo.db) for every carried show
	$(UV) python tools/fixtures/generate.py --out .fixtures --demo
	mkdir -p data
	cd server && uv run python -m retro_cable.demo --db $(DEMO_DB) --catalog ../catalog --media ../.fixtures

demo: demo-db ## Play with the app on demo media: http://127.0.0.1:5173
	@trap 'kill 0' EXIT; \
	(cd server && RC_DB=$(DEMO_DB) RC_MEDIA_DIR=$(CURDIR)/.fixtures \
		uv run uvicorn retro_cable.app:app --host 127.0.0.1 --port 8000 --no-access-log) & \
	(cd web && npm run dev -- --host 127.0.0.1) & \
	wait

lint: lint-server lint-web ## ruff, mypy --strict, tsc, eslint

lint-server:
	cd server && uv run ruff check $(PY_PATHS)
	cd server && uv run ruff format --check $(PY_PATHS)
	cd server && uv run mypy $(PY_PATHS)

lint-web:
	cd web && npm run typecheck
	cd web && npm run lint

test: test-server test-tools test-ops test-web e2e ## Everything

test-server: ## Server unit and contract tests
	cd server && uv run pytest

test-tools: ## Fixture generator tests (needs ffmpeg)
	cd server && uv run pytest ../tools/fixtures

test-ops: ## Compose, Caddy and deploy script tests
	cd server && uv run pytest ../ops/tests

test-web: ## Vitest unit tests
	cd web && npm test

e2e: ## Playwright against a local API and production web build
	cd web && npx playwright test --project=chromium

catalog-check: ## Validate catalog/*.yaml (schemas and cross-file checks)
	cd server && uv run python -m retro_cable.catalog ../catalog

catalog-schema: ## Regenerate catalog/schema/*.json from the models
	cd server && uv run python -m retro_cable.catalog.schema ../catalog/schema

ingest: ## Import from Plex, then fill gaps from TMDB (needs PLEX_URL, PLEX_TOKEN, TMDB_TOKEN)
	cd server && uv run python -m retro_cable.ingest plex --db $(RC_DB) --catalog ../catalog
	cd server && uv run python -m retro_cable.ingest tmdb --db $(RC_DB)

inventory: ## Write reports/inventory.md and .json from the catalog DB
	cd server && uv run python -m retro_cable.inventory --db $(RC_DB) --catalog ../catalog --out ../reports

api: ## Regenerate server/openapi.json and the web client
	cd server && uv run python -m retro_cable.openapi openapi.json
	cd web && npm run api

api-check: api ## Fail if the committed spec or client is stale
	git diff --exit-code -- server/openapi.json web/src/api/generated

stack-up: fixtures ## Build and start ops/compose.test.yml on 127.0.0.1:8080 (demo feeds)
	test -f .fixtures/_demo/manifest.json || $(MAKE) demo-db
	RC_GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE_TEST) up -d --build --wait

stack-down:
	$(COMPOSE_TEST) down -v

e2e-stack: ## Playwright (Chromium + WebKit) against the compose test stack
	cd web && BASE_URL=http://127.0.0.1:8080 npx playwright test
