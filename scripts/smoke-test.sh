#!/usr/bin/env bash
# ---------------------------------------------------------------
# smoke-test.sh: proves the M1 stack really works, end to end.
# A "smoke test" is a quick check that the main things switch on
# and do their basic job (named after "turn it on, see if it smokes").
#
# For each service it does a real action, not just "is it running":
#   - every container reports "healthy"
#   - TimescaleDB: create a hypertable, insert a reading, read it back
#   - Mosquitto:   publish a message and receive it on a subscriber
#   - Mailpit:     send an email and find it in the inbox
#   - Caddy:       fetch the health endpoint and the status page
#
# Usage: ./scripts/smoke-test.sh   (or: make smoke)
# Needs: docker, curl. Everything else runs inside the containers.
# ---------------------------------------------------------------
set -uo pipefail

failures=0
run_id="$(date +%s)-$RANDOM" # unique per run, so old data can't fake a pass

pass() { printf "  ✅ %s\n" "$1"; }
fail() { printf "  ❌ %s\n" "$1"; failures=$((failures + 1)); }

echo ""
echo "AquaPulse smoke test (M1)"
echo ""

# 1. Container health -------------------------------------------------------
for svc in mqtt db mailpit caddy; do
  cid="$(docker compose ps -q "$svc" 2>/dev/null)"
  status="$( [ -n "$cid" ] && docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null )"
  if [ "$status" = "healthy" ]; then pass "$svc container is healthy"; else fail "$svc container is '${status:-not running}' (try: make logs)"; fi
done

# 2. TimescaleDB: a real hypertable round trip, rolled back afterwards -----
sql="BEGIN;
CREATE TABLE smoke_readings (time timestamptz NOT NULL, device_id text NOT NULL, litres numeric);
SELECT create_hypertable('smoke_readings', 'time');
INSERT INTO smoke_readings VALUES (now(), 'smoke-$run_id', 12.5);
SELECT litres FROM smoke_readings WHERE device_id = 'smoke-$run_id';
ROLLBACK;"
db_out="$(docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -qtA -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<<"$sql" 2>&1)"
if grep -qx "12.5" <<<"$db_out"; then
  ts_version="$(docker compose exec -T db sh -c 'psql -qtA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extversion FROM pg_extension WHERE extname = '"'"'timescaledb'"'"'"' 2>/dev/null)"
  pass "TimescaleDB $ts_version: hypertable created, reading stored and read back"
else
  fail "TimescaleDB round trip failed: $db_out"
fi

# 3. Mosquitto: publish -> subscribe round trip ------------------------------
msg="hello-$run_id"
mqtt_out="$(docker compose exec -T mqtt sh -c "
  mosquitto_sub -h localhost -t 'aquapulse/smoke' -C 1 -W 5 &
  sleep 1
  mosquitto_pub -h localhost -t 'aquapulse/smoke' -m '$msg'
  wait" 2>&1)"
if [ "$mqtt_out" = "$msg" ]; then pass "Mosquitto: message published and received"; else fail "Mosquitto round trip failed: $mqtt_out"; fi

# 4. Mailpit: send an email, find it through the API --------------------------
subject="AquaPulse smoke test $run_id"
printf 'From: aquapulse@localhost\nTo: owner@example.com\nSubject: %s\n\nIf you can read this, email works.\n' "$subject" \
  | docker compose exec -T mailpit /mailpit sendmail -S localhost:1025 owner@example.com >/dev/null 2>&1
# Mailpit stores the email a moment after receiving it, so look for up to 10 s.
found=""
for _ in $(seq 1 10); do
  if curl -fsS "http://localhost:8025/api/v1/search?query=subject:%22smoke%20test%20${run_id}%22" 2>/dev/null | grep -q '"messages_count":1'; then
    found=yes; break
  fi
  sleep 1
done
if [ -n "$found" ]; then
  pass "Mailpit: email sent and found in the inbox (http://localhost:8025)"
else
  fail "Mailpit: test email not found"
fi

# 5. Caddy: health endpoint and status page ----------------------------------
if [ "$(curl -fsS http://localhost:8080/healthz 2>/dev/null)" = "ok" ] && curl -fsS http://localhost:8080/ | grep -q "AquaPulse"; then
  pass "Caddy: status page served at http://localhost:8080"
else
  fail "Caddy: http://localhost:8080 did not answer correctly"
fi

echo ""
if [ "$failures" -eq 0 ]; then
  echo "All M1 checks passed. 🎉"
else
  echo "$failures check(s) failed. See docs/milestones/M1-infrastructure.md → Troubleshooting."
fi
echo ""
exit "$failures"
