# ---------------------------------------------------------------
# AquaPulse Makefile: short names for longer commands.
# Type `make` or `make help` to see everything you can run.
# New targets are added milestone by milestone (see docs/milestones).
# ---------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help check-tools setup env hooks lint secrets up down ps logs smoke reset psql mqtt-watch \
	sim-logs sim-profile sim-test sim-test-live leak valve-close valve-open test

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
	@echo ""
	@echo "Stack is up: status page http://localhost:8080 · email inbox http://localhost:8025"
	@echo "             simulator http://localhost:8090/docs"
	@echo "Next: make smoke"

down: ## Stop the stack (your data is kept)
	docker compose down

ps: ## Show the containers and their health
	docker compose ps

logs: ## Follow the logs of all services (Ctrl+C to stop)
	docker compose logs -f --tail=50

smoke: ## Prove every service works (database, MQTT, email, web, simulator)
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

test: sim-test ## Run all unit tests (grows with each milestone)
