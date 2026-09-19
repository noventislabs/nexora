# NEXORA AI AUTOPILOT — common development commands.
.PHONY: help setup api worker web migrate revision test test-api test-web lint fmt up down logs

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd apps/web && npm install

migrate: ## Apply database migrations
	cd apps/api && .venv/bin/alembic upgrade head

revision: ## Create a new migration (make revision m="add table")
	cd apps/api && .venv/bin/alembic revision --autogenerate -m "$(m)"

api: ## Run the API with reload
	cd apps/api && .venv/bin/uvicorn nexora.main:app --reload --port 8000

worker: ## Run the background worker
	cd apps/api && .venv/bin/python -m nexora.queue.worker

web: ## Run the frontend
	cd apps/web && npm run dev

test: test-api test-web ## Run every test suite

test-api: ## Run backend tests
	cd apps/api && .venv/bin/python -m pytest

test-web: ## Run frontend tests
	cd apps/web && npm test

lint: ## Lint backend and typecheck frontend
	cd apps/api && .venv/bin/ruff check nexora tests
	cd apps/web && npx tsc --noEmit

fmt: ## Auto-fix backend lint
	cd apps/api && .venv/bin/ruff check --fix nexora tests

up: ## Start the full stack in Docker
	docker compose up --build

down: ## Stop the stack
	docker compose down
