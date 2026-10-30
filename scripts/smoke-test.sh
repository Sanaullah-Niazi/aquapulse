#!/usr/bin/env bash
# ---------------------------------------------------------------
# smoke-test.sh: proves the stack really works, end to end (M1 to M4).
# A "smoke test" is a quick check that the main things switch on
# and do their basic job (named after "turn it on, see if it smokes").
#
# For each service it does a real action, not just "is it running":
#   - every container reports "healthy"
#   - TimescaleDB: create a hypertable, insert a reading, read it back
#   - Mosquitto:   publish a message and receive it on a subscriber
#   - Mailpit:     send an email and find it in the inbox
#   - Caddy:       fetch the health endpoint and the status page
#   - Simulator:   10 homes + 1 tank online, all sending telemetry (M2)
#   - API:         ready, demo login works, /metrics private, audit log
#                  protected from the API's own database role (M3)
#   - Ingestor:    fresh readings from every device in the database, a
#                  real valve close/open answered by the device, a live
#                  WebSocket event, consumption totals (M4)
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
echo "AquaPulse smoke test (M1 to M4)"
echo ""

# 1. Container health -------------------------------------------------------
for svc in mqtt db mailpit caddy simulator api ingestor; do
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

# 6. Simulator (M2): every virtual device online and sending telemetry ---------
expected=11 # 10 homes + 1 tank (default SIM_HOMES=10)
# Retained status messages: the broker hands us the latest one per device at once.
online="$(docker compose exec -T mqtt mosquitto_sub -h localhost -t 'aquapulse/v1/dev/+/status' -v -W 3 2>/dev/null \
  | grep -c '"state":"online"')"
if [ "$online" -ge "$expected" ]; then
  pass "Simulator: $online devices online (retained MQTT status)"
else
  fail "Simulator: only ${online:-0} of $expected devices online (try: make sim-logs)"
fi
# Telemetry is sent every 10 s, so 15 s is enough to hear from every device.
senders="$(docker compose exec -T mqtt mosquitto_sub -h localhost -t 'aquapulse/v1/dev/+/telemetry' -v -W 15 2>/dev/null \
  | cut -d/ -f4 | sort -u | wc -l | tr -d ' ')"
if [ "$senders" -ge "$expected" ]; then
  pass "Simulator: telemetry received from all $senders devices (watch: make mqtt-watch)"
else
  fail "Simulator: telemetry from only ${senders:-0} of $expected devices"
fi
if curl -fsS http://localhost:8090/healthz 2>/dev/null | grep -q '"healthy":true'; then
  pass "Simulator: control API answers at http://localhost:8090/docs"
else
  fail "Simulator: control API at http://localhost:8090 did not answer"
fi

# 7. API (M3) ----------------------------------------------------------------
migrate_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(docker compose ps -aq migrate 2>/dev/null)" 2>/dev/null)"
if [ "$migrate_exit" = "0" ]; then pass "Database migrations applied (migrate job finished OK)"; else fail "migrate job failed (try: docker compose logs migrate)"; fi

ready="$(curl -sS http://localhost:8080/api/readyz 2>/dev/null)"
if [ "$ready" = '{"database":true,"broker":true}' ]; then
  pass "API: ready through Caddy, database + broker reachable (http://localhost:8080/api/docs)"
else
  fail "API: /api/readyz said '${ready:-nothing}' (try: make api-logs)"
fi

# Demo accounts (safe to repeat), then a real login with the password from .env.
docker compose exec -T api python -m app.seed >/dev/null 2>&1
demo_password="$(grep '^DEMO_PASSWORD=' .env | cut -d= -f2-)"
login="$(curl -sS -X POST http://localhost:8080/api/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\":\"owner@example.com\",\"password\":\"$demo_password\"}" 2>/dev/null)"
token="$(sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p' <<<"$login")"
buildings="$(curl -sS http://localhost:8080/api/v1/buildings -H "Authorization: Bearer $token" 2>/dev/null)"
if [ -n "$token" ] && grep -q "AquaPulse Demo Tower" <<<"$buildings"; then
  pass "API: owner@example.com logged in and sees the demo building"
else
  fail "API: demo login failed (did DEMO_PASSWORD in .env change after make seed?)"
fi

if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/metrics)" = "404" ] \
  && docker compose exec -T api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/metrics').read().decode())" 2>/dev/null | grep -q aquapulse_http_requests_total; then
  pass "API: /metrics works inside Docker and is hidden from outside"
else
  fail "API: /metrics check failed"
fi

audit_out="$(docker compose exec -T db sh -c 'psql -qtA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SET ROLE aquapulse_app; DELETE FROM audit_log;"' 2>&1)"
if grep -q "permission denied" <<<"$audit_out"; then
  pass "Audit log: the API's database role cannot delete history"
else
  fail "Audit log: DELETE was not refused: $audit_out"
fi

# 8. Ingestor, valve control and live stream (M4) ------------------------------
# Devices report every 10 s; right after `make seed` the first ones can take that long.
for _ in $(seq 1 30); do
  fresh="$(docker compose exec -T db sh -c 'psql -qtA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT (SELECT count(DISTINCT device_id) FROM telemetry WHERE time > now() - interval '"'"'1 minute'"'"')
         + (SELECT count(DISTINCT device_id) FROM tank_readings WHERE time > now() - interval '"'"'1 minute'"'"')"' 2>/dev/null)"
  [ "${fresh:-0}" -ge "$expected" ] && break
  sleep 1
done
if [ "${fresh:-0}" -ge "$expected" ]; then
  pass "Ingestor: readings from all $fresh devices stored in the last minute (make ingest-logs)"
else
  fail "Ingestor: fresh readings from only ${fresh:-0} of $expected devices (try: make ingest-logs)"
fi

# A real valve close and open on unit 03, through the API, answered by the
# (simulated) device, while a WebSocket listens like the app will.
m4_out="$(docker compose exec -T api python - <<'PY' 2>&1
import asyncio, json, os, urllib.request
from websockets.asyncio.client import connect

API = "http://127.0.0.1:8000/api/v1"

def call(method, path, token=None, body=None):
    request = urllib.request.Request(API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} | ({"Authorization": "Bearer " + token} if token else {}))
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)

async def main():
    token = call("POST", "/auth/login", body={"email": "owner@example.com",
                 "password": os.environ["DEMO_PASSWORD"]})["access_token"]
    building = call("GET", "/buildings", token)[0]
    home = next(h for h in call("GET", f"/buildings/{building['id']}/homes", token) if h["unit_no"] == "03")
    ticket = call("POST", "/ws-ticket", token)["ticket"]
    async with connect(f"ws://127.0.0.1:8000/api/v1/ws?ticket={ticket}") as ws:
        await ws.recv()  # "ready"
        results = []
        for action in ("close", "open"):
            command = call("POST", f"/homes/{home['id']}/valve", token,
                           {"action": action, "reason": "smoke test"})
            for _ in range(40):
                await asyncio.sleep(0.5)
                command = call("GET", f"/commands/{command['id']}", token)
                if command["state"] not in ("queued", "pending"):
                    break
            results.append(f"{action}={command['state']}")
        events = []
        try:
            while True:
                events.append(json.loads(await asyncio.wait_for(ws.recv(), 12))["type"])
                if "telemetry" in events and "command" in events:
                    break
        except TimeoutError:
            pass
    print("VALVE", " ".join(results))
    print("EVENTS", " ".join(sorted(set(events))))
    usage = call("GET", f"/buildings/{building['id']}/consumption?granularity=day", token)
    print("CONSUMPTION", len(usage["homes"]), usage["total_litres"])

asyncio.run(main())
PY
)"
if grep -q "VALVE close=succeeded open=succeeded" <<<"$m4_out"; then
  pass "Valve: unit 03 closed and opened again, confirmed by the device's ack"
else
  fail "Valve: close/open did not succeed: $(grep VALVE <<<"$m4_out" || tail -n 3 <<<"$m4_out")"
fi
if grep -q "EVENTS .*command.*telemetry" <<<"$m4_out"; then
  pass "Live stream: WebSocket delivered command and telemetry events (try: make live)"
else
  fail "Live stream: missing events: $(grep EVENTS <<<"$m4_out" || tail -n 3 <<<"$m4_out")"
fi
if grep -qE "CONSUMPTION 10 [0-9.]+" <<<"$m4_out"; then
  pass "Consumption: daily litres for all 10 homes ($(grep CONSUMPTION <<<"$m4_out" | cut -d' ' -f3) L in the last 30 days)"
else
  fail "Consumption: $(grep CONSUMPTION <<<"$m4_out" || tail -n 3 <<<"$m4_out")"
fi

echo ""
if [ "$failures" -eq 0 ]; then
  echo "All checks passed. 🎉"
else
  echo "$failures check(s) failed. See the Troubleshooting parts of the guides in docs/milestones/."
fi
echo ""
exit "$failures"
