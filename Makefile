# ---------------------------------------------------------------
# AquaPulse Makefile: short names for longer commands.
# Type `make` or `make help` to see everything you can run.
# New targets are added milestone by milestone (see docs/milestones).
# ---------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help check-tools setup env hooks lint secrets up down ps logs smoke reset psql mqtt-watch \
	sim-logs sim-profile sim-test sim-test-live leak valve-close valve-open test \
	api-logs seed migrate api-test api-lint openapi \
	ingest-logs demo-data live

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nAquaPulse commands:\n\n"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

check-tools: ## Check that every M0 tool is installed (with versions)
	@./scripts/check-tools.sh

setup: env hooks ## First-time setup after cloning: create .env and install git hooks

env: ## Create or update .env from .env.example (never changes values you already have)
	@./scripts/sync-env.sh

hooks: ## Install the pre-commit git hooks
	pre-commit install

lint: ## Run all pre-commit checks on every file
	pre-commit run --all-files

secrets: ## Scan the whole git history for leaked secrets
	gitleaks git --config .gitleaks.toml --verbose .

# ---------------------------------------------------------------
# M1: Docker stack
# ---------------------------------------------------------------

up: env ## Start the whole stack and wait until every service is healthy
	docker compose up -d --build --wait
	@# Caddy does not notice a changed Caddyfile by itself: reload it (harmless if unchanged).
	@docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force >/dev/null 2>&1 || true
	@echo ""
	@echo "Stack is up: status page http://localhost:8080 · email inbox http://localhost:8025"
	@echo "             simulator http://localhost:8090/docs · API http://localhost:8080/api/docs"
	@echo "Next: make smoke"

down: ## Stop the stack (your data is kept)
	docker compose down

ps: ## Show the containers and their health
	docker compose ps

logs: ## Follow the logs of all services (Ctrl+C to stop)
	docker compose logs -f --tail=50

smoke: ## Prove every service works (database, MQTT, email, web, simulator, API)
	@./scripts/smoke-test.sh

reset: ## DANGER: stop the stack and DELETE all its data (asks first)
	@read -p "This deletes ALL AquaPulse data (database, MQTT, Caddy, simulator counters). Type 'yes' to continue: " ans; \
	if [ "$$ans" = "yes" ]; then docker compose down -v && echo "All data deleted."; else echo "Cancelled."; fi

psql: ## Open a SQL prompt inside the database (type \q to quit)
	docker compose exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

mqtt-watch: ## Print every MQTT message on aquapulse/# (Ctrl+C to stop)
	docker compose exec mqtt mosquitto_sub -h localhost -t 'aquapulse/#' -v

# ---------------------------------------------------------------
# M2: Simulator (digital twin)
# ---------------------------------------------------------------

sim-logs: ## Follow the simulator's logs (Ctrl+C to stop)
	docker compose logs -f --tail=50 simulator

sim-profile: ## Print a week of simulated water use per home (morning/evening peaks)
	docker compose exec simulator aquapulse-sim profile --days 7

DEVICE ?= home-b01-03
leak: ## Start a 2-hour leak on a home: make leak (or make leak DEVICE=home-b01-07)
	docker compose exec simulator aquapulse-sim scenario leak $(DEVICE) --minutes 120

valve-close: ## Close a home's water valve over MQTT: make valve-close DEVICE=home-b01-03
	docker compose exec simulator aquapulse-sim send $(DEVICE) valve close

valve-open: ## Open a home's water valve again: make valve-open DEVICE=home-b01-03
	docker compose exec simulator aquapulse-sim send $(DEVICE) valve open

sim-test: ## Run the simulator's unit tests (no Docker needed)
	cd simulator && uv run pytest

sim-test-live: ## Run the live MQTT tests against the running stack (make up first)
	cd simulator && uv run pytest -m integration -v

# ---------------------------------------------------------------
# M3: Backend API
# ---------------------------------------------------------------

api-logs: ## Follow the API's logs (Ctrl+C to stop)
	docker compose logs -f --tail=50 api

seed: ## Create the demo accounts and building (safe to run again)
	docker compose exec api python -m app.seed

migrate: ## Apply new database migrations now (make up does this too)
	docker compose run --rm migrate

api-test: ## Run the API tests (needs the database: make up first)
	cd backend && uv run pytest

api-lint: ## Check the backend code style (ruff)
	cd backend && uv run ruff check . && uv run ruff format --check .

openapi: ## Regenerate backend/openapi.json after changing the API
	cd backend && uv run python -m app.export_openapi

# ---------------------------------------------------------------
# M4: Live data and valve control
# ---------------------------------------------------------------

ingest-logs: ## Follow the ingestor's logs (Ctrl+C to stop)
	docker compose logs -f --tail=50 ingestor

DAYS ?= 90
demo-data: ## Load 90 days of demo history (safe to run again): make demo-data (or DAYS=30)
	@echo "Making $(DAYS) days of history with the simulator and loading it (1 to 3 minutes)..."
	docker compose run --rm -T --no-deps simulator aquapulse-sim backfill --days $(DAYS) \
		| docker compose exec -T api python -m app.backfill

WHO ?= owner@example.com
live: ## Print live events like an app sees them: make live (or WHO=resident03@example.com)
	docker compose exec api python -m app.watch $(WHO)

test: sim-test api-test ## Run all tests (grows with each milestone)
