#!/usr/bin/env bash
# ---------------------------------------------------------------
# zap-baseline.sh (M12, NFR-10): OWASP ZAP looks at the running web app
# the way an attacker's tools would, without attacking it (a "passive"
# baseline scan): missing security headers, cookies without flags, leaked
# version numbers, and so on.
#
# Fails on any High risk finding. Medium and Low findings are printed so
# they can be reviewed (and listed in docs/security/scan-exceptions.md).
# The full report: zap-report.html in infra/zap/ (git-ignored).
# Usage: make zap   (the stack must be running: make up)
# ---------------------------------------------------------------
set -euo pipefail

target="${1:-https://localhost:8443/app/}"
dir="infra/zap"
mkdir -p "$dir"
chmod 777 "$dir" # ZAP runs as its own user inside the container and writes here
rm -f "$dir/zap-report.json" "$dir/zap-report.html"

echo "ZAP baseline scan of $target (about 2 minutes)..."
# -I: do not stop on warnings; the check below decides. The scan trusts our
# own CA's certificate because ZAP accepts any certificate by default.
docker run --rm --network host -v "$PWD/$dir:/zap/wrk:rw" zaproxy/zap-stable:2.16.1 \
  zap-baseline.py -t "$target" -I -J zap-report.json -r zap-report.html >"$dir/zap-output.txt" 2>&1 || {
  cat "$dir/zap-output.txt"
  echo "ZAP itself could not run" >&2
  exit 1
}

python3 - "$dir/zap-report.json" <<'PY'
import json, sys

names = {"3": "High", "2": "Medium", "1": "Low", "0": "Info"}
alerts = [a for site in json.load(open(sys.argv[1]))["site"] for a in site["alerts"]]
alerts.sort(key=lambda a: -int(a["riskcode"]))
for a in alerts:
    print(f"  {names[a['riskcode']]:6} {a['name']} ({a['count']} URLs)")
high = [a for a in alerts if a["riskcode"] == "3"]
counts = {n: sum(1 for a in alerts if names[a["riskcode"]] == n) for n in names.values()}
print("ZAP findings: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
if high:
    sys.exit("ZAP found a High risk problem (see above and infra/zap/zap-report.html)")
print("No High risk findings.")
PY
