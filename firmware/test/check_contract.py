"""Checks that every message the firmware makes matches the device contract.

The C++ test program prints one example of each message (--dump); this script
validates them with the same schemas and validator as the simulator (FR-09,
FR-10). Run: make firmware-test
"""

from __future__ import annotations

import json
import sys

from aquapulse_sim.contract import validate


def main() -> int:
    problems = 0
    seen: set[str] = set()
    for line in sys.stdin:
        if not line.strip():
            continue
        schema, payload = line.rstrip("\n").split("\t", 1)
        seen.add(schema)
        if len(payload.encode()) > 2048:
            print(f"FAIL {schema}: longer than 2 KB")
            problems += 1
        errors = validate(schema, json.loads(payload))
        for e in errors:
            print(f"FAIL {schema}: {e}\n     {payload}")
        problems += len(errors)
    expected = {"telemetry-home.v1", "telemetry-tank.v1", "status.v1", "state-home.v1",
                "state-tank.v1", "ack.v1"}  # fmt: skip
    missing = expected - seen
    if missing:
        print(f"FAIL: no example of {sorted(missing)}")
        problems += 1
    print(
        f"firmware messages: {len(seen)} kinds checked against the contract, {problems} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
