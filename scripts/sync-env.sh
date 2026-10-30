#!/usr/bin/env bash
# ---------------------------------------------------------------
# sync-env.sh: keeps your private .env in step with .env.example.
#
# - Creates .env if it doesn't exist.
# - Adds any setting that is in .env.example but missing from .env
#   (so new milestones can add settings without you copying by hand).
# - A value of __GENERATE__ becomes a fresh random secret, unique to
#   your machine. Existing values in .env are never changed, except an
#   old default that a later milestone replaced (listed below, and shown).
# Usage: ./scripts/sync-env.sh   (or: make env)
# ---------------------------------------------------------------
set -euo pipefail

example=".env.example"
target=".env"

[ -f "$target" ] || { touch "$target"; echo "Created $target"; }

added=0
while IFS= read -r line || [ -n "$line" ]; do
  # Skip comments and blank lines.
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  # Already set in .env? Leave it alone.
  grep -qE "^${key}=" "$target" && continue
  if [ "$value" = "__GENERATE__" ]; then
    value="$(openssl rand -hex 24)"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$target"
  echo "  + added $key"
  added=$((added + 1))
done < "$example"

# Old defaults that a milestone replaced: update them only if you never
# changed them yourself. M5: the site moved to HTTPS.
retired=(
  "PUBLIC_URL=http://localhost:8080|PUBLIC_URL=https://localhost:8443"
)
for pair in "${retired[@]}"; do
  old="${pair%%|*}"
  new="${pair#*|}"
  if grep -qxF "$old" "$target"; then
    tmp="$(mktemp)"
    awk -v old="$old" -v new="$new" '$0 == old { print new; next } { print }' "$target" > "$tmp"
    cat "$tmp" > "$target" && rm -f "$tmp"
    echo "  ~ updated ${new%%=*} (new default: ${new#*=})"
    added=$((added + 1))
  fi
done

if [ "$added" -eq 0 ]; then
  echo ".env is up to date"
else
  echo "Added $added setting(s) to .env"
fi
