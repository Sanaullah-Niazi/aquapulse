#!/usr/bin/env bash
# ---------------------------------------------------------------
# check-tools.sh: prints a ✅ or ❌ for every tool AquaPulse needs in M0.
# Usage: ./scripts/check-tools.sh   (or: make check-tools)
# It only reads versions; it never installs or changes anything.
# ---------------------------------------------------------------
set -u

missing=0

# check <label> <command...>: runs the command and shows the first line of output.
# A tool counts as missing if the command doesn't exist or exits with an error.
check() {
  local label="$1"; shift
  local out
  if command -v "$1" >/dev/null 2>&1 && out=$("$@" 2>/dev/null); then
    printf "  ✅ %-16s %s\n" "$label" "$(printf '%s\n' "$out" | head -n 1)"
  else
    printf "  ❌ %-16s not found or not working (see docs/milestones/M0-tools-and-repo.md)\n" "$label"
    missing=$((missing + 1))
  fi
}

echo ""
echo "Required for M0:"
check "git"          git --version
check "GitHub CLI"   gh --version
check "uv"           uv --version
check "Python 3.12"  uv run --no-project --python 3.12 python --version
check "Node.js"      node --version
check "pnpm"         pnpm --version
check "Docker"       docker --version
check "Docker engine" docker info --format 'running, server {{.ServerVersion}}'
check "pre-commit"   pre-commit --version
check "gitleaks"     gitleaks version
check "make"         make --version
check "VS Code CLI"  code --version

echo ""
if [ "$missing" -eq 0 ]; then
  echo "All M0 tools are ready. 🎉"
else
  echo "$missing tool(s) missing. Install them, then run this again."
fi
echo ""
exit "$missing"
