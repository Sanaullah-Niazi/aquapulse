#!/usr/bin/env bash
# ---------------------------------------------------------------
# sync-env.sh: keeps your private .env in step with .env.example.
#
# - Creates .env if it doesn't exist.
# - Adds any setting that is in .env.example but missing from .env
#   (so new milestones can add settings without you copying by hand).
# - A value of __GENERATE__ becomes a fresh random secret, unique to
#   your machine. Existing values in .env are never changed.
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

if [ "$added" -eq 0 ]; then
  echo ".env is up to date"
else
  echo "Added $added setting(s) to .env"
fi
