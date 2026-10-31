# ---------------------------------------------------------------
# AquaPulse Makefile: short names for longer commands.
# Type `make` or `make help` to see everything you can run.
# New targets are added milestone by milestone (see docs/milestones).
# ---------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help check-tools setup env hooks lint secrets up down ps logs smoke reset psql mqtt-watch \
	sim-logs sim-profile sim-test sim-test-live leak valve-close valve-open test \
	api-logs seed migrate api-test api-lint openapi \
	ingest-logs demo-data live \
	certs trust-ca devices sim-passwords broker-logs tls-check restart-check \
	worker-logs bills problem alerts \
	web-setup web-dev web-build web-test web-e2e web-site web-site-check app-test apk apk-install admin admin-reset-2fa

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nAquaPulse commands:\n\n"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
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

up: env certs ## Start the whole stack and wait until every service is healthy
	docker compose up -d --build --wait
	@# Caddy does not notice a changed Caddyfile by itself: reload it (harmless if unchanged).
	@docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force >/dev/null 2>&1 || true
	@echo ""
	@echo "Stack is up: status page https://localhost:8443 · email inbox http://localhost:8025"
	@echo "             simulator http://localhost:8090/docs · API https://localhost:8443/api/docs"
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
	docker compose exec mqtt sh -c 'mosquitto_sub -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
		-u monitor -P "$$MQTT_MONITOR_PASSWORD" -t "aquapulse/#" -v'

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

valve-close: ## Close a home's water valve through the API: make valve-close DEVICE=home-b01-03
	docker compose exec api python -m app.valve $(DEVICE) close

valve-open: ## Open a home's water valve again: make valve-open DEVICE=home-b01-03
	docker compose exec api python -m app.valve $(DEVICE) open

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

admin: ## Make a System Admin: make admin EMAIL=you@example.com (asks for a password)
	@test -n "$(EMAIL)" || (echo "Use: make admin EMAIL=you@example.com" && exit 1)
	docker compose exec $(if $(ADMIN_PASSWORD),-e ADMIN_PASSWORD) api python -m app.admins add "$(EMAIL)"

admin-reset-2fa: ## Let a System Admin who lost their phone set up two-factor login again
	@test -n "$(EMAIL)" || (echo "Use: make admin-reset-2fa EMAIL=you@example.com" && exit 1)
	docker compose exec api python -m app.admins reset-2fa "$(EMAIL)"

migrate: ## Apply new database migrations now (make up does this too)
	docker compose run --rm migrate

api-test: ## Run the API tests (needs the database: make up first)
	@# The PDF tests need Pango (`brew install pango`); on Apple Silicon Python finds it here.
	cd backend && DYLD_FALLBACK_LIBRARY_PATH="$${DYLD_FALLBACK_LIBRARY_PATH:-/opt/homebrew/lib}" uv run pytest

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
	@# M6: history for the second demo building (KWD), which has no live devices.
	docker compose run --rm -T --no-deps -e SIM_BUILDING=b02 -e SIM_HOMES=3 -e SIM_SEED=7 \
		-e DEMO_TIMEZONE=Asia/Kuwait simulator aquapulse-sim backfill --days $(DAYS) \
		| docker compose exec -T api python -m app.backfill --history-only

WHO ?= owner@example.com
live: ## Print live events like an app sees them: make live (or WHO=resident03@example.com)
	docker compose exec api python -m app.watch $(WHO)

# ---------------------------------------------------------------
# M5: Secure MQTT and HTTPS
# ---------------------------------------------------------------

certs: ## Create the project CA and server certificate in infra/certs (once; safe to repeat)
	@./scripts/make-certs.sh

trust-ca: certs ## Optional: let your Mac's browsers trust https://localhost:8443 (asks for your password)
	sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain infra/certs/public/ca.crt
	@echo "Done. Restart your browser. Undo: Keychain Access > System > AquaPulse Local CA > Delete"

devices: ## List the demo building's devices and whether they are online
	@docker compose exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "SELECT id, status, online, last_seen_at::timestamp(0) AS last_seen, mqtt_password_hash IS NOT NULL AS own_password FROM devices ORDER BY id"'

sim-passwords: ## Give the simulated devices back their simulator passwords (after trying rotation)
	docker compose exec api python -m app.seed --sim-passwords

broker-logs: ## Follow the broker's logs: logins, refused logins, refused topics (Ctrl+C to stop)
	docker compose logs -f --tail=50 mqtt broker-auth

tls-check: ## Check TLS on the broker and the web server, and that port 1883 is closed
	@./scripts/tls-check.sh

restart-check: ## Restart everything and check it is all healthy again within 90 s
	@./scripts/restart-check.sh

# ---------------------------------------------------------------
# M6: Billing and payments
# ---------------------------------------------------------------

worker-logs: ## Follow the worker's logs: bills, alerts, emails, audit checks (Ctrl+C to stop)
	docker compose logs -f --tail=50 worker

MONTH ?=
bills: ## Make last month's draft bills for every building now: make bills (or MONTH=2026-09)
	docker compose exec api python -m app.bills $(MONTH)

# ---------------------------------------------------------------
# M7: Alerts and notifications
# ---------------------------------------------------------------

TYPE ?= temp_spike
MINUTES ?= 5
problem: ## Cause a real problem for the alert engine: make problem (or TYPE=ph_drift DEVICE=home-b01-02 MINUTES=30)
	docker compose exec simulator aquapulse-sim scenario $(TYPE) $(DEVICE) --minutes $(MINUTES) $(if $(filter temp_spike,$(TYPE)),--params '{"deltaC": 25}')

alerts: ## List the newest alerts (open ones first) straight from the database
	@docker compose exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "SELECT severity, state, rule, COALESCE(device_id, '"'"'-'"'"') AS device, opened_at::timestamp(0) AS opened, seen_count AS seen, left(message, 60) AS message FROM alerts ORDER BY state IN ('"'"'open'"'"', '"'"'acknowledged'"'"') DESC, opened_at DESC LIMIT 20"'

# ---------------------------------------------------------------
# M8: Web app (React PWA in web/). make up already serves it at
# https://localhost:8443/app/ ; these are for working on it.
# ---------------------------------------------------------------

web-setup: ## Install the web app's tools once (packages and the test browser)
	cd web && pnpm install --frozen-lockfile && pnpm exec playwright install chromium

web-dev: ## Run the web app with instant reload at http://localhost:5173/app/ (make up first)
	cd web && pnpm dev

web-build: ## Build the web app into web/dist (what make up serves)
	cd web && pnpm build

web-test: ## Type check and unit tests for the web app (no Docker needed)
	cd web && pnpm typecheck && pnpm test

web-e2e: ## Click through the app in a real browser, phone and desktop size (make up first)
	@# The tests log in as the demo accounts, so they need the demo password from .env.
	cd web && DEMO_PASSWORD="$$(grep '^DEMO_PASSWORD=' ../.env | cut -d= -f2-)" pnpm e2e

web-site: ## Build the app for your website: make web-site BASE=/water/ API=https://your-server
	./scripts/web-site.sh "$(BASE)" "$(API)"

web-site-check: ## Try the website build on your Mac at http://localhost:8099/water/ (make up first)
	./scripts/web-site-check.sh

# ---------------------------------------------------------------
# M9: Android app (Flutter in mobile/). Needs Flutter and Android Studio
# (brew install --cask flutter android-studio). See docs/milestones/M9.
# ---------------------------------------------------------------

app-test: ## Lint and widget tests for the Android app (no phone or Docker needed)
	cd mobile && flutter pub get && flutter analyze && flutter test

apk: certs ## Build the Android app (APK) that also trusts your Mac's AquaPulse certificate
	@# The app trusts Android's normal certificates plus this CA (only this
	@# project's own, never "trust everything"). The copy is git-ignored.
	cp infra/certs/public/ca.crt mobile/assets/certs/local-ca.crt
	cd mobile && flutter pub get && flutter build apk --release
	@echo "APK: mobile/build/app/outputs/flutter-apk/app-release.apk"

apk-install: ## Install the APK on the running emulator or USB phone and connect it to your Mac
	@# adb reverse: on the phone, https://localhost:8443 now reaches your Mac's port 8443.
	adb reverse tcp:8443 tcp:8443
	adb install -r mobile/build/app/outputs/flutter-apk/app-release.apk
	adb shell monkey -p io.github.sanaullahniazi.aquapulse -c android.intent.category.LAUNCHER 1 >/dev/null
	@echo "AquaPulse is open on the phone. Server: https://localhost:8443"

test: sim-test api-test web-test app-test ## Run all tests (grows with each milestone)
