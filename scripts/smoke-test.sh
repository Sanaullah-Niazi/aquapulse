#!/usr/bin/env bash
# ---------------------------------------------------------------
# smoke-test.sh: proves the stack really works, end to end (M1 to M6).
# A "smoke test" is a quick check that the main things switch on
# and do their basic job (named after "turn it on, see if it smokes").
#
# For each service it does a real action, not just "is it running":
#   - every container reports "healthy"
#   - TimescaleDB: create a hypertable, insert a reading, read it back
#   - Mosquitto:   a TLS round trip with passwords; refused logins, a refused
#                  topic and a closed plain port 1883 (M5)
#   - Mailpit:     send an email and find it in the inbox
#   - Caddy:       HTTPS status page, HTTP -> HTTPS redirect, security headers (M5)
#   - Simulator:   10 homes + 1 tank online, all sending telemetry (M2)
#   - API:         ready, demo login works, /metrics private, audit log
#                  protected from the API's own database role (M3)
#   - Ingestor:    fresh readings from every device in the database, a
#                  real valve close/open answered by the device, a live
#                  WebSocket event, consumption totals (M4)
#   - Devices:     register a device, it connects within 10 s; rotate and
#                  revoke its password, the broker refuses it within 10 s (M5)
#   - Billing:     a tariff, a month of readings, a generated KWD bill with
#                  the right total, its PDF, a part payment and the rest (M6)
#
# Usage: ./scripts/smoke-test.sh   (or: make smoke)
# Needs: docker, curl. Everything else runs inside the containers.
# ---------------------------------------------------------------
set -uo pipefail

failures=0
web="https://localhost:8443"
ca="infra/certs/public/ca.crt" # curl checks Caddy's certificate with our CA (make certs)
setting() { grep "^$1=" .env | cut -d= -f2-; }
run_id="$(date +%s)-$RANDOM" # unique per run, so old data can't fake a pass

pass() { printf "  ✅ %s\n" "$1"; }
fail() { printf "  ❌ %s\n" "$1"; failures=$((failures + 1)); }

echo ""
echo "AquaPulse smoke test (M1 to M6)"
echo ""

# 1. Container health -------------------------------------------------------
for svc in broker-auth mqtt db mailpit caddy simulator api ingestor worker; do
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

# 3. Mosquitto (M5: TLS only, a password for everyone, rules per topic) -------
# home-test-01 is a device account only for tests (password made from
# SIM_DEVICE_SECRET); the read-only monitor account listens.
test_pw="$(docker compose exec -T -e SIM_DEVICE_SECRET="$(setting SIM_DEVICE_SECRET)" api python -c \
  "import os; from app.security.mqtt_passwords import simulator_password as p; print(p(os.environ['SIM_DEVICE_SECRET'], 'home-test-01'))" 2>/dev/null)"
monitor_pw="$(setting MQTT_MONITOR_PASSWORD)"
tls_opts="-h localhost -p 8883 --cafile /mosquitto/certs/ca.crt"
msg="hello-$run_id"
mqtt_out="$(docker compose exec -T mqtt sh -c "
  mosquitto_sub $tls_opts -u monitor -P '$monitor_pw' -t 'aquapulse/v1/dev/home-test-01/status' -C 1 -W 5 &
  sleep 1
  mosquitto_pub $tls_opts -u home-test-01 -P '$test_pw' -t 'aquapulse/v1/dev/home-test-01/status' -m '$msg'
  wait" 2>&1)"
if [ "$mqtt_out" = "$msg" ]; then pass "Mosquitto: message sent and received over TLS, with passwords"; else fail "Mosquitto TLS round trip failed: $mqtt_out"; fi

refused="$(docker compose exec -T mqtt sh -c "
  mosquitto_pub $tls_opts -t aquapulse/x -m x 2>&1 | grep -c 'not authorised'
  mosquitto_pub $tls_opts -u home-test-01 -P wrong-password -t aquapulse/x -m x 2>&1 | grep -c 'not authorised'" 2>/dev/null | tr -d '\r' | paste -sd' ' -)"
if [ "$refused" = "1 1" ]; then pass "Mosquitto: no password or a wrong password is refused"; else fail "Mosquitto: a login without the right password was not refused ($refused)"; fi

# A device publishing to ANOTHER home's topic: the broker answers "Not authorized".
denied="$(docker compose exec -T mqtt mosquitto_pub $tls_opts -u home-test-01 -P "$test_pw" -V 5 -q 1 \
  -t aquapulse/v1/dev/home-b01-02/telemetry -m forged 2>&1)"
if grep -q "Not authorized" <<<"$denied"; then pass "Mosquitto: home-test-01 may not publish to home-b01-02's topic (\"$denied\")"; else fail "Mosquitto: a device could publish to another home's topic: $denied"; fi

if docker compose exec -T mqtt mosquitto_pub -h localhost -p 1883 -t x -m x >/dev/null 2>&1; then
  fail "Mosquitto: plain MQTT port 1883 is open"
else
  pass "Mosquitto: plain port 1883 is closed, TLS port 8883 only"
fi

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

# 5. Caddy: HTTPS, redirect and security headers (M5) --------------------------
if curl -fsS --cacert "$ca" "$web/" 2>/dev/null | grep -q "AquaPulse"; then
  pass "Caddy: status page served over HTTPS at $web"
else
  fail "Caddy: $web did not answer correctly (did make certs run?)"
fi
redirect="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost:8080/api/docs 2>/dev/null)"
if [ "$redirect" = "301 $web/api/docs" ]; then pass "Caddy: http://localhost:8080 redirects to HTTPS"; else fail "Caddy: no HTTPS redirect ($redirect)"; fi
headers="$(curl -sSI --cacert "$ca" "$web/" 2>/dev/null | tr -d '\r')"
missing=""
for h in strict-transport-security content-security-policy x-content-type-options referrer-policy x-frame-options; do
  grep -qi "^$h:" <<<"$headers" || missing="$missing $h"
done
if [ -z "$missing" ]; then pass "Caddy: security headers (HSTS, CSP, nosniff, referrer, frame) are sent"; else fail "Caddy: missing headers:$missing"; fi

# 6. Simulator (M2): every virtual device online and sending telemetry ---------
expected=11 # 10 homes + 1 tank (default SIM_HOMES=10)
# Retained status messages: the broker hands us the latest one per device at once.
online="$(docker compose exec -T mqtt mosquitto_sub $tls_opts -u monitor -P "$monitor_pw" -t 'aquapulse/v1/dev/+/status' -v -W 3 2>/dev/null \
  | grep -v '^aquapulse/v1/dev/home-test-01/' | grep -c '"state":"online"')"
if [ "$online" -ge "$expected" ]; then
  pass "Simulator: $online devices online (retained MQTT status)"
else
  fail "Simulator: only ${online:-0} of $expected devices online (try: make sim-logs)"
fi
# Telemetry is sent every 10 s, so 15 s is enough to hear from every device.
senders="$(docker compose exec -T mqtt mosquitto_sub $tls_opts -u monitor -P "$monitor_pw" -t 'aquapulse/v1/dev/+/telemetry' -v -W 15 2>/dev/null \
  | cut -d/ -f4 | grep -v '^home-test-01$' | sort -u | wc -l | tr -d ' ')"
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

ready="$(curl -sS --cacert "$ca" "$web/api/readyz" 2>/dev/null)"
if [ "$ready" = '{"database":true,"broker":true}' ]; then
  pass "API: ready through Caddy, database + broker (TLS) reachable ($web/api/docs)"
else
  fail "API: /api/readyz said '${ready:-nothing}' (try: make api-logs)"
fi

# Demo accounts (safe to repeat), then a real login with the password from .env.
docker compose exec -T api python -m app.seed >/dev/null 2>&1
demo_password="$(setting DEMO_PASSWORD)"
login="$(curl -sS --cacert "$ca" -X POST "$web/api/v1/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"owner@example.com\",\"password\":\"$demo_password\"}" 2>/dev/null)"
token="$(sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p' <<<"$login")"
buildings="$(curl -sS --cacert "$ca" "$web/api/v1/buildings" -H "Authorization: Bearer $token" 2>/dev/null)"
if [ -n "$token" ] && grep -q "AquaPulse Demo Tower" <<<"$buildings"; then
  pass "API: owner@example.com logged in and sees the demo building"
else
  fail "API: demo login failed (did DEMO_PASSWORD in .env change after make seed?)"
fi

if [ "$(curl -s --cacert "$ca" -o /dev/null -w '%{http_code}' "$web/metrics")" = "404" ] \
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
    building = next(b for b in call("GET", "/buildings", token) if b["name"] == "AquaPulse Demo Tower")
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

# 9. Device registration, rotation and revoke (M5, FR-14) ----------------------
# On a temporary home (archived again at the end): the real API, the real
# broker, a real TLS login as the new device. Each step must take <= 10 s.
m5_out="$(docker compose exec -T api python - "$run_id" <<'PY' 2>&1
import asyncio, os, ssl, sys, time, uuid
import aiomqtt
from app.watch import call

TLS = ssl.create_default_context(cafile="/certs/ca.crt")

def broker(user, password):
    return aiomqtt.Client("mqtt", 8883, identifier=f"smoke-{uuid.uuid4().hex[:8]}",
                          username=user, password=password, tls_context=TLS, timeout=5)

async def can_login(user, password):
    try:
        async with broker(user, password):
            return True
    except aiomqtt.MqttError:
        return False

async def seconds_until(check, start, limit=10.0):
    """Seconds from `start` (when the API was called) until check() is true."""
    while time.monotonic() - start <= limit:
        if await check():
            return f"{time.monotonic() - start:.1f}s"
        await asyncio.sleep(0.5)
    return "never"

async def kicked(client, topic):
    try:
        await client.publish(topic, "still here?", qos=1, timeout=2)
        return False
    except aiomqtt.MqttError:
        return True

async def main():
    token = call("POST", "/auth/login", {"email": "owner@example.com",
                 "password": os.environ["DEMO_PASSWORD"]})["access_token"]
    building = next(b for b in call("GET", "/buildings", token=token)
                    if b["name"] == "AquaPulse Demo Tower")
    home = call("POST", f"/buildings/{building['id']}/homes",
                {"unit_no": f"SMOKE-{sys.argv[1]}"[:20], "occupants": 0}, token=token)
    try:
        start = time.monotonic()
        first = call("POST", f"/homes/{home['id']}/device", token=token)
        user, old = first["username"], first["password"]
        print("REGISTERED", await seconds_until(lambda: can_login(user, old), start))
        topic = f"aquapulse/v1/dev/{user}/status"
        async with broker(user, old) as connected:
            start = time.monotonic()
            new = call("POST", f"/devices/{user}/password", token=token)["password"]
            print("ROTATED", await seconds_until(lambda: kicked(connected, topic), start),
                  await seconds_until(lambda: _not(can_login(user, old)), start),
                  await seconds_until(lambda: can_login(user, new), start))
        async with broker(user, new) as connected:
            start = time.monotonic()
            call("POST", f"/devices/{user}/revoke", token=token)
            print("REVOKED", await seconds_until(lambda: kicked(connected, topic), start),
                  await seconds_until(lambda: _not(can_login(user, new)), start))
    finally:
        call("DELETE", f"/homes/{home['id']}", token=token)

async def _not(check):
    return not await check

asyncio.run(main())
PY
)"
reg="$(grep REGISTERED <<<"$m5_out" | cut -d' ' -f2)"
if [ -n "$reg" ] && [ "$reg" != "never" ]; then pass "Devices: a newly registered device logged in after $reg (no broker restart)"; else fail "Devices: new device could not log in: $(tail -n 3 <<<"$m5_out")"; fi
read -r _ old_kicked old_refused new_ok <<<"$(grep ROTATED <<<"$m5_out")"
if [ -n "${new_ok:-}" ] && ! grep -q never <<<"$old_kicked $old_refused $new_ok"; then
  pass "Devices: password rotated: old connection cut after $old_kicked, old password refused, new one works"
else
  fail "Devices: rotation: $(grep ROTATED <<<"$m5_out" || tail -n 3 <<<"$m5_out")"
fi
read -r _ revoked_kicked revoked_refused <<<"$(grep REVOKED <<<"$m5_out")"
if [ -n "${revoked_refused:-}" ] && ! grep -q never <<<"$revoked_kicked $revoked_refused"; then
  pass "Devices: revoked device disconnected after $revoked_kicked and refused from then on"
else
  fail "Devices: revoke: $(grep REVOKED <<<"$m5_out" || tail -n 3 <<<"$m5_out")"
fi

# 10. Billing (M6) ---------------------------------------------------------------
# In a building kept for smoke tests ("Smoke Test Building", KWD): a new home
# and resident each run, last month's readings, then the real API: generate,
# issue, PDF, a part payment and the rest. The home is archived at the end.
m6_out="$(docker compose exec -T api python - "$run_id" <<'PY' 2>&1
import asyncio, os, sys, uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import urllib.request
from sqlalchemy import select
from app import db
from app.backfill import refresh_consumption
from app.models import Occupancy, Telemetry, User
from app.watch import call

API = "http://127.0.0.1:8000/api/v1"

def pdf(path, token):
    request = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

async def add_resident_and_water(home_id, building_id, first, days, zone):
    """A billing contact and 7,000 L over last month, straight into the database."""
    async with db.sessions()() as session, session.begin():
        user = User(email=f"smoke-{uuid.uuid4().hex[:8]}@example.com", full_name="Smoke Resident",
                    role="resident", password_hash=None)
        session.add(user)
        await session.flush()
        session.add(Occupancy(home_id=home_id, user_id=user.id, move_in=first - timedelta(days=30),
                              is_billing_contact=True))
        boot, total, rows = uuid.uuid4(), 500.0, []
        for n in range(days + 1):
            at = datetime.combine(first + timedelta(days=n - 1), time(12), zone)
            delta = 0.0 if n == 0 else 7000.0 / days
            total += delta
            rows.append(Telemetry(time=at, device_id="home-smoke-01", boot_id=boot, seq=n,
                home_id=home_id, building_id=building_id, total_l=total, delta_l=delta,
                flow_lpm=0, ph=7.2, temp_c=24, valve="open", rssi=-60))
        session.add_all(rows)
    await refresh_consumption(rows[0].time, rows[-1].time)

async def main():
    token = call("POST", "/auth/login", {"email": "owner@example.com",
                 "password": os.environ["DEMO_PASSWORD"]})["access_token"]
    building = next((b for b in call("GET", "/buildings", token=token)
                     if b["name"] == "Smoke Test Building"), None)
    if building is None:
        building = call("POST", "/buildings", {"name": "Smoke Test Building", "timezone": "Asia/Kuwait",
                        "currency_code": "KWD"}, token=token)
    if not call("GET", f"/buildings/{building['id']}/tariffs", token=token):
        call("POST", f"/buildings/{building['id']}/tariffs", {
            "name": "Smoke tariff", "effective_from": "2020-01-01",
            "slabs": [{"from_l": 0, "to_l": 5000, "rate_per_kl": "0.250"},
                      {"from_l": 5000, "to_l": None, "rate_per_kl": "0.500"}],
            "charges": [{"name": "Sewerage", "kind": "extra_percent", "value": "10"}]}, token=token)
    zone = ZoneInfo(building["timezone"])
    today = datetime.now(zone).date()
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    days = (today.replace(day=1) - first).days
    home = call("POST", f"/buildings/{building['id']}/homes",
                {"unit_no": f"SMOKE-{sys.argv[1]}"[:20], "occupants": 1}, token=token)
    try:
        await add_resident_and_water(uuid.UUID(home["id"]), uuid.UUID(building["id"]), first, days, zone)
        call("POST", f"/buildings/{building['id']}/bills/generate", {"month": f"{first:%Y-%m}"}, token=token)
        bill = next(b for b in call("GET", f"/buildings/{building['id']}/bills?month={first:%Y-%m}", token=token)
                    if b["home_id"] == home["id"])
        print("BILL", bill["status"], bill["litres"], bill["total_text"].replace(" ", "_"))
        bill = call("POST", f"/bills/{bill['id']}/issue", token=token)
        print("ISSUED", bill["bill_no"], bill["due_date"])
        try:  # a PDF problem should not hide the payment checks below
            document = pdf(f"/bills/{bill['id']}/pdf", token)
            print("PDF", document[:5].decode(), len(document))
        except Exception as error:
            print("PDF failed:", error)
        call("POST", f"/bills/{bill['id']}/payments", {"amount": "1.000", "method": "cash"}, token=token)
        part = call("GET", f"/bills/{bill['id']}", token=token)["status"]
        call("POST", f"/bills/{bill['id']}/payments", {"amount": "1.475", "method": "bank"}, token=token)
        print("PAID", part, call("GET", f"/bills/{bill['id']}", token=token)["status"])
    finally:
        call("DELETE", f"/homes/{home['id']}", token=token)
        await db.dispose()

asyncio.run(main())
PY
)"
if grep -q "BILL draft 7000.000 KWD_2.475" <<<"$m6_out"; then
  pass "Billing: last month's bill generated from the readings: 7,000 L = KWD 2.475 (3 decimals)"
else
  fail "Billing: generation: $(grep BILL <<<"$m6_out" || tail -n 3 <<<"$m6_out")"
fi
read -r _ bill_no due <<<"$(grep ISSUED <<<"$m6_out")"
if [[ "${bill_no:-}" =~ ^B[0-9]+-[0-9]{4}-[0-9]{6}$ ]]; then pass "Billing: issued as $bill_no, due $due"; else fail "Billing: issue: $(grep ISSUED <<<"$m6_out" || tail -n 3 <<<"$m6_out")"; fi
read -r _ magic size _ <<<"$(grep '^PDF' <<<"$m6_out")"
if [ "${magic:-}" = "%PDF-" ] && [ "${size:-0}" -gt 5000 ]; then pass "Billing: PDF bill downloaded ($((size / 1024)) KB)"; else fail "Billing: PDF: $(grep '^PDF' <<<"$m6_out" || tail -n 3 <<<"$m6_out")"; fi
if grep -q "PAID partially_paid paid" <<<"$m6_out"; then
  pass "Billing: cash part payment -> partially paid; bank transfer of the rest -> paid"
else
  fail "Billing: payments: $(grep PAID <<<"$m6_out" || tail -n 3 <<<"$m6_out")"
fi

echo ""
if [ "$failures" -eq 0 ]; then
  echo "All checks passed. 🎉"
else
  echo "$failures check(s) failed. See the Troubleshooting parts of the guides in docs/milestones/."
fi
echo ""
exit "$failures"
