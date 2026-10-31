#!/usr/bin/env bash
# ---------------------------------------------------------------
# Check the website build on your Mac before you upload it (FR-56).
#
#   make web-site-check        (make up first)
#
# 1. Builds the app for the folder /water/ (make web-site), talking to the
#    API on your Mac (https://localhost:8443).
# 2. Serves it like GitHub Pages at http://localhost:8099/water/: another
#    address than the API, just like your real website.
# 3. Lets that address call the API (CORS) for the length of the check: the
#    API restarts with it added, and restarts with your .env again at the end.
# 4. A real browser installs-checks it, logs in, sees live data and opens a
#    link straight to a page (web/e2e-site/site.spec.ts).
# ---------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

SITE="http://localhost:8099"
current="$(grep '^CORS_ORIGINS=' .env | cut -d= -f2- || true)"

./scripts/web-site.sh /water/ https://localhost:8443

restore() {
  echo "Putting the API back to the CORS_ORIGINS in your .env..."
  docker compose up -d --wait api >/dev/null 2>&1
}
trap restore EXIT
echo "Letting $SITE call the API for this check..."
CORS_ORIGINS="${current:-https://localhost:8443},$SITE" docker compose up -d --wait api >/dev/null 2>&1

cd web
DEMO_PASSWORD="$(grep '^DEMO_PASSWORD=' ../.env | cut -d= -f2-)" \
  pnpm exec playwright test -c playwright.site.config.ts
