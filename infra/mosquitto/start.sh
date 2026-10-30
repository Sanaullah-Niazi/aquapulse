#!/bin/sh
# ---------------------------------------------------------------
# Starts Mosquitto and keeps its password list up to date (M5).
#
# 1. Copies the certificates, access rules and password list to places
#    only the "mosquitto" user can read (Mosquitto refuses files that
#    everyone can read).
# 2. Starts the broker.
# 3. Every second, checks whether broker-auth wrote a new password list.
#    If so, copies it and sends the broker a "reload" signal (SIGHUP).
#    On reload Mosquitto also disconnects clients whose password is no
#    longer valid, so a rotated or revoked device is cut off at once.
# ---------------------------------------------------------------
set -eu

SOURCE=/broker-auth/passwd # written by the broker-auth service
AUTH=/mosquitto/auth
CERTS=/mosquitto/certs

mkdir -p "$AUTH" "$CERTS"
install -m 600 -o mosquitto -g mosquitto /mosquitto/config/acl "$AUTH/acl"
for f in ca.crt server.crt server.key; do
  install -m 600 -o mosquitto -g mosquitto "/certs/$f" "$CERTS/$f"
done

# Wait (at most 60 s) for broker-auth's first password list.
waited=0
while [ ! -s "$SOURCE" ] && [ "$waited" -lt 60 ]; do
  sleep 1
  waited=$((waited + 1))
done
if [ -s "$SOURCE" ]; then
  install -m 600 -o mosquitto -g mosquitto "$SOURCE" "$AUTH/passwd"
else
  echo "start.sh: no password list from broker-auth yet; nobody can log in until it arrives"
  install -m 600 -o mosquitto -g mosquitto /dev/null "$AUTH/passwd"
fi

/usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf &
broker=$!
# `docker compose stop` sends TERM: pass it on so the broker saves its data.
trap 'kill -TERM "$broker"; wait "$broker"; exit 0' TERM INT

while kill -0 "$broker" 2>/dev/null; do
  if [ -s "$SOURCE" ] && ! cmp -s "$SOURCE" "$AUTH/passwd"; then
    install -m 600 -o mosquitto -g mosquitto "$SOURCE" "$AUTH/passwd"
    kill -HUP "$broker"
    echo "start.sh: password list changed, broker reloaded ($(wc -l < "$AUTH/passwd") accounts)"
  fi
  sleep 1
done
wait "$broker"
