#!/usr/bin/env bash
# ---------------------------------------------------------------
# restart-check.sh: NFR-17, "everything is healthy again within 90 s
# after a restart". Restarts every container at once (like a server
# reboot), then waits for all health checks, which include: the broker
# accepts TLS logins, every simulated device is reconnected, and the
# ingestor is connected again.
# Usage: ./scripts/restart-check.sh   (or: make restart-check)
# ---------------------------------------------------------------
set -uo pipefail

limit=90
start=$(date +%s)
docker compose restart >/dev/null 2>&1
if docker compose up -d --wait --wait-timeout "$limit" >/dev/null 2>&1; then
  echo "  ✅ All services and devices healthy again $(($(date +%s) - start)) s after a full restart (limit $limit s)"
else
  echo "  ❌ Not healthy within $limit s after a restart:"
  docker compose ps --format '     {{.Service}}: {{.Status}}'
  exit 1
fi
