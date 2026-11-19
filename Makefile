# One-word entry points (DESIGN 4.5). Run from the repo root of any worktree.
SHELL := /bin/bash
COMPOSE := docker compose
PY_DIRS := api db worker
API_PORT = $(shell grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2)

.PHONY: help up down migrate seed test lint logs ps health check-env

help:
	@echo "make up | down | migrate | seed | test | lint | logs | ps | health"

check-env:
	@test -f .env || { echo "Missing .env: cp .env.example .env and set the ports for this worktree (AGENTS.md)"; exit 1; }

up: check-env
	$(COMPOSE) up -d --build --wait
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

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps
