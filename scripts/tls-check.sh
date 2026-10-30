#!/usr/bin/env bash
# ---------------------------------------------------------------
# tls-check.sh: checks encryption on the running stack (M5, NFR-07).
#
#   1. Port scan: the plain MQTT port 1883 is closed, inside and outside.
#   2. openssl: the broker (8883) and Caddy (443) speak TLS 1.3 with a
#      certificate that our CA signed for that name.
#   3. testssl.sh: a well-known free scanner looks for old protocols
#      (SSLv3, TLS 1.0/1.1), weak ciphers and known TLS attacks.
#
# Both tools run in a container inside the stack's Docker network, so
# nothing needs installing. Takes about a minute.
# Usage: ./scripts/tls-check.sh   (or: make tls-check)
# ("docker compose port" prints ":0" when a port is not published.)
# ---------------------------------------------------------------
set -uo pipefail

failures=0
pass() { printf "  ✅ %s\n" "$1"; }
fail() { printf "  ❌ %s\n" "$1"; failures=$((failures + 1)); }

image="drwetter/testssl.sh:3.2" # testssl.sh (free, GPLv2) with its own OpenSSL 3
network="${COMPOSE_PROJECT_NAME:-aquapulse}_default"
in_network() {
  docker run --rm --network "$network" -v "$PWD/infra/certs/public:/ca:ro" --entrypoint "$1" "$image" "${@:2}"
}

echo ""
echo "AquaPulse TLS check"
echo ""

# 1. Port 1883 -----------------------------------------------------------------
if ! docker compose port mqtt 1883 2>/dev/null | grep -qE ":[1-9][0-9]*$" \
  && ! docker compose exec -T mqtt mosquitto_pub -h localhost -p 1883 -t x -m x >/dev/null 2>&1; then
  pass "Port 1883 (plain MQTT) is closed; only 8883 (TLS) is open"
else
  fail "Port 1883 (plain MQTT) answers"
fi

# 2. TLS version and certificate -------------------------------------------------
for target in mqtt:8883 caddy:443; do
  host="${target%%:*}"
  out="$(in_network openssl s_client -connect "$target" -servername "$host" -CAfile /ca/ca.crt \
    -verify_return_error -verify_hostname "$host" -brief </dev/null 2>&1)"
  if grep -q "Protocol version: TLSv1.3" <<<"$out" && grep -q "Verification: OK" <<<"$out"; then
    pass "$target: TLS 1.3, certificate signed by the AquaPulse CA for '$host'"
  else
    fail "$target: $(grep -E 'Protocol|Verif|error' <<<"$out" | head -n 2 | tr '\n' ' ')"
  fi
done

# 3. testssl.sh -----------------------------------------------------------------
for target in mqtt:8883 caddy:443; do
  out="$(in_network testssl.sh --quiet --color 0 --add-ca /ca/ca.crt --protocols --std --vulnerable "$target" 2>&1)"
  problems="$(grep -E "VULNERABLE|offered \(NOT ok\)|^ TLS 1(\.1)? +offered" <<<"$out" | grep -v "not vulnerable")"
  if grep -q "Done" <<<"$out" && [ -z "$problems" ]; then
    pass "$target: testssl.sh found no old protocols, weak ciphers or known attacks"
  else
    fail "$target: testssl.sh: ${problems:-did not finish}"
  fi
done

echo ""
if [ "$failures" -eq 0 ]; then echo "TLS check passed. 🔒"; else echo "$failures TLS check(s) failed."; fi
echo ""
exit "$failures"
