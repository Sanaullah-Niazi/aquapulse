# ---------------------------------------------------------------
# AquaPulse Makefile: short names for longer commands.
# Type `make` or `make help` to see everything you can run.
# New targets are added milestone by milestone (see docs/milestones).
# ---------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help check-tools setup env hooks lint secrets

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nAquaPulse commands:\n\n"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

check-tools: ## Check that every M0 tool is installed (with versions)
	@./scripts/check-tools.sh

setup: env hooks ## First-time setup after cloning: create .env and install git hooks

env: ## Create .env from .env.example (never overwrites an existing .env)
	@if [ -f .env ]; then echo ".env already exists, leaving it alone"; else cp .env.example .env && echo "Created .env from .env.example"; fi

hooks: ## Install the pre-commit git hooks
	pre-commit install

lint: ## Run all pre-commit checks on every file
	pre-commit run --all-files

secrets: ## Scan the whole git history for leaked secrets
	gitleaks git --config .gitleaks.toml --verbose .
