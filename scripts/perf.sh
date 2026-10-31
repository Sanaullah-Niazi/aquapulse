#!/usr/bin/env bash
# ---------------------------------------------------------------
# perf.sh (M12): speed and resource checks on the running demo.
#   1. NFR-06: memory and CPU of the whole stack while idle (docker stats,
#      one sample every 10 s for IDLE seconds; the SRS asks for 10 minutes).
#   2. NFR-01, NFR-05, NFR-04: valve round trip, live update delay and read
#      speed (backend/app/perf.py, inside the API container).
# It prints what it measured. It does not fail: the results go into
# docs/test-report.md by hand, with the date and where they were measured.
# Usage: make perf            (IDLE=600 for the full 10 minutes)
# ---------------------------------------------------------------
set -euo pipefail

idle="${IDLE:-120}"
samples=$((idle / 10))
echo "Resources (NFR-06): whole stack while idle, $samples samples over $idle s..."
mem_max=0
cpu_sum=0
for _ in $(seq 1 "$samples"); do
  # One line per container: "123.4MiB / 7.6GiB;1.23%"
  # shellcheck disable=SC2046 # one argument per container id
  read -r mem cpu < <(docker stats --no-stream --format '{{.MemUsage}};{{.CPUPerc}}' \
    $(docker compose ps -q) | python3 -c '
import sys
units = {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}
mem = cpu = 0.0
for line in sys.stdin:
    usage, percent = line.strip().split(";")
    used = usage.split("/")[0].strip()
    number = used.rstrip("BKMGiTkb")
    mem += float(number) * units[used[len(number):]]
    cpu += float(percent.rstrip("%"))
print(f"{mem:.0f} {cpu:.2f}")')
  mem_max=$(python3 -c "print(max($mem_max, $mem))")
  cpu_sum=$(python3 -c "print($cpu_sum + $cpu)")
  sleep 9
done
echo "  memory, all containers together: highest $mem_max MiB (limit 2048 MiB)"
echo "  CPU, all containers together: average $(python3 -c "print(round($cpu_sum / $samples, 1))")% of one core (limit 15%)"
echo "  machine: $(nproc 2>/dev/null || sysctl -n hw.ncpu) CPU cores, $(uname -sm)"
echo ""

docker compose exec -T api python -m app.perf
