# One-word entry points (DESIGN 4.5). Run from the repo root of any worktree.
SHELL := /bin/bash
COMPOSE := docker compose
PY_DIRS := api db worker
API_PORT = $(shell grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2)

.PHONY: help up down migrate seed test lint logs ps health check-env worker

help:
	@echo "make up | down | migrate | seed | test | lint | logs | ps | health | worker"

check-env:
	@test -f .env || { echo "Missing .env: cp .env.example .env and set the ports for this worktree (AGENTS.md)"; exit 1; }

# `migrate` is a one-shot service; `up --wait` treats its exit as a failure, so run it first.
up: check-env
	@if $(COMPOSE) config --services | grep -qx migrate; then $(COMPOSE) up -d --wait db && $(COMPOSE) run --rm --build migrate; fi
	$(COMPOSE) up -d --build --wait $$($(COMPOSE) config --services | grep -vx migrate)
	@$(MAKE) --no-print-directory health

down:
	$(COMPOSE) down

migrate: check-env
	@test -f db/Dockerfile || { echo "db/ is not ready yet (db agent)"; exit 1; }
	$(COMPOSE) run --rm migrate

seed: check-env
	@test -f db/Dockerfile || { echo "db/ is not ready yet (db agent)"; exit 1; }
	$(COMPOSE) run --rm migrate seed

health: check-env
	curl -fsS http://127.0.0.1:$(API_PORT)/api/health && echo

test:
	@set -e; for d in $(PY_DIRS); do \
	  if [ -f $$d/pyproject.toml ]; then echo "== $$d: pytest"; (cd $$d && uv run pytest -q); fi; \
	done
	@if [ -f web/package.json ]; then echo "== web: npm test"; (cd web && npm test); fi

lint:
	@set -e; for d in $(PY_DIRS); do \
	  if [ -f $$d/pyproject.toml ]; then echo "== $$d: ruff"; (cd $$d && uv run ruff check . && uv run ruff format --check .); fi; \
	done
	@if [ -f web/package.json ]; then echo "== web: lint"; (cd web && npm run lint && npm run typecheck); fi
	@if [ -f contracts/openapi.yaml ]; then echo "== contracts: redocly"; npx --yes @redocly/cli@latest lint contracts/openapi.yaml; fi

# Real local worker on the host (DESIGN 4.5): uses the host `claude` sign-in, owner jobs only.
# Polls every 10 s for queued jobs (scheduler or "Generate now"); Ctrl-C stops it.
# AGENT_RUNNER is cleared so each job uses its own runner (in .env it selects the api's local stub).
worker: check-env
	@set -a; . ./.env; set +a; cd worker && while true; do \
	  API_BASE_URL=http://127.0.0.1:$$API_PORT AGENT_RUNNER= uv run brief-worker || true; sleep 10; \
	done

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps
