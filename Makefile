# ---------------------------------------------------------------
# AquaPulse Makefile: short names for longer commands.
# Type `make` or `make help` to see everything you can run.
# New targets are added milestone by milestone (see docs/milestones).
# ---------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help check-tools setup env hooks lint secrets up down ps logs smoke reset psql mqtt-watch

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
	docker compose up -d --wait
	@echo ""
	@echo "Stack is up: status page http://localhost:8080 · email inbox http://localhost:8025"
	@echo "Next: make smoke"

down: ## Stop the stack (your data is kept)
	docker compose down

ps: ## Show the containers and their health
	docker compose ps

logs: ## Follow the logs of all services (Ctrl+C to stop)
	docker compose logs -f --tail=50

smoke: ## Prove every service works (database, MQTT, email, web)
	@./scripts/smoke-test.sh

reset: ## DANGER: stop the stack and DELETE all its data (asks first)
	@read -p "This deletes ALL AquaPulse data (database, MQTT, Caddy). Type 'yes' to continue: " ans; \
	if [ "$$ans" = "yes" ]; then docker compose down -v && echo "All data deleted."; else echo "Cancelled."; fi

psql: ## Open a SQL prompt inside the database (type \q to quit)
	docker compose exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

mqtt-watch: ## Print every MQTT message on aquapulse/# (Ctrl+C to stop)
	docker compose exec mqtt mosquitto_sub -h localhost -t 'aquapulse/#' -v
