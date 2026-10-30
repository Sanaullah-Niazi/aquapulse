#!/usr/bin/env bash
# ---------------------------------------------------------------
# make-certs.sh: creates the project's own certificate authority (CA)
# and one server certificate for the MQTT broker and Caddy (M5, FR-12).
#
# In simple words: a certificate is an ID card for a server. The CA is
# the "office" that signs ID cards. Devices, the simulator and your
# browser are told to trust our CA, so they can check that they really
# talk to our broker / web server and not to an impostor.
#
#   infra/certs/ca/       ca.key + ca.crt   the CA. ca.key NEVER leaves this
#                                           folder (no container gets it).
#   infra/certs/server/   server.key/.crt   the broker's and Caddy's ID card
#                         + ca.crt
#   infra/certs/public/   ca.crt            what clients need to check it
#
# Everything is git-ignored. Safe to run again: existing files are kept
# unless you pass --force (then everything is made new; restart the stack).
# Usage: ./scripts/make-certs.sh [--force]   (or: make certs)
# Needs: openssl (OpenSSL 3 or the LibreSSL that comes with macOS).
# ---------------------------------------------------------------
set -euo pipefail

dir="infra/certs"
days_ca=3650    # the CA is valid for 10 years
days_server=825 # the longest that Apple devices accept for a server certificate

if [ "${1:-}" = "--force" ]; then
  rm -rf "$dir"
fi
if [ -f "$dir/server/server.crt" ] && [ -f "$dir/public/ca.crt" ]; then
  echo "Certificates already exist in $dir (use --force to make new ones)"
  exit 0
fi

# Homebrew's OpenSSL 3 if it is installed, otherwise the system one.
openssl_bin="openssl"
if command -v brew >/dev/null 2>&1 && [ -x "$(brew --prefix openssl@3 2>/dev/null)/bin/openssl" ]; then
  openssl_bin="$(brew --prefix openssl@3)/bin/openssl"
fi

mkdir -p "$dir/ca" "$dir/server" "$dir/public"
chmod 700 "$dir/ca"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# 1. The CA: an elliptic-curve key (P-256: small, fast and strong) and a
#    self-signed certificate that may only sign other certificates.
"$openssl_bin" ecparam -name prime256v1 -genkey -noout -out "$dir/ca/ca.key"
cat >"$tmp/ca.ext" <<'EOF'
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
"$openssl_bin" req -new -key "$dir/ca/ca.key" -subj "/O=AquaPulse/CN=AquaPulse Local CA" -out "$tmp/ca.csr"
"$openssl_bin" x509 -req -in "$tmp/ca.csr" -signkey "$dir/ca/ca.key" -days "$days_ca" -sha256 \
  -extfile "$tmp/ca.ext" -out "$dir/ca/ca.crt" 2>/dev/null

# 2. The server certificate, signed by the CA. "subjectAltName" lists every
#    name clients use to reach the server: localhost on your Mac, and the
#    service names inside the Docker network (mqtt, caddy).
"$openssl_bin" ecparam -name prime256v1 -genkey -noout -out "$dir/server/server.key"
cat >"$tmp/server.ext" <<'EOF'
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, DNS:mqtt, DNS:caddy, IP:127.0.0.1, IP:::1
authorityKeyIdentifier = keyid
EOF
"$openssl_bin" req -new -key "$dir/server/server.key" -subj "/O=AquaPulse/CN=localhost" -out "$tmp/server.csr"
"$openssl_bin" x509 -req -in "$tmp/server.csr" -CA "$dir/ca/ca.crt" -CAkey "$dir/ca/ca.key" \
  -CAcreateserial -CAserial "$tmp/ca.srl" -days "$days_server" -sha256 \
  -extfile "$tmp/server.ext" -out "$dir/server/server.crt" 2>/dev/null

cp "$dir/ca/ca.crt" "$dir/server/ca.crt"
cp "$dir/ca/ca.crt" "$dir/public/ca.crt"
chmod 600 "$dir/ca/ca.key"
# The containers copy the key and give it to their own user, so it only needs
# to be readable here (it never leaves your computer).
chmod 644 "$dir/server/server.key"

"$openssl_bin" verify -CAfile "$dir/ca/ca.crt" "$dir/server/server.crt" >/dev/null
echo "Created the AquaPulse CA and server certificate in $dir"
echo "  CA fingerprint: $("$openssl_bin" x509 -in "$dir/ca/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
echo "  Optional: make trust-ca  (your Mac's browsers then trust https://localhost:8443)"
