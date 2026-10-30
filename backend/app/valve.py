"""Close or open a home's valve through the API: `make valve-close DEVICE=...`.

Logs in as the demo owner, sends the command the way the app will, and waits
for the device's answer (FR-24). Since M5 only the server's `commander`
account may publish commands, so this goes through the API (and the audit
log) instead of straight to MQTT.

    python -m app.valve home-b01-03 close
"""

from __future__ import annotations

import os
import sys
import time

from .watch import call


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in ("open", "close"):
        sys.exit("usage: python -m app.valve DEVICE_ID open|close")
    device_id, action = sys.argv[1], sys.argv[2]
    login = {"email": "owner@example.com", "password": os.environ["DEMO_PASSWORD"]}
    token = call("POST", "/auth/login", login)["access_token"]
    devices = [
        d
        for building in call("GET", "/buildings", token=token)
        for d in call("GET", f"/buildings/{building['id']}/devices", token=token)
    ]
    device = next((d for d in devices if d["id"] == device_id and d["home_id"]), None)
    if device is None:
        sys.exit(f"No home device called {device_id}. Devices: make devices")
    body = {"action": action, "reason": "make valve-" + action}
    command = call("POST", f"/homes/{device['home_id']}/valve", body, token=token)
    print(f"sent: {action} to {device_id} (command {command['id']})")
    deadline = time.monotonic() + 20
    while command["state"] in ("queued", "pending") and time.monotonic() < deadline:
        time.sleep(0.5)
        command = call("GET", f"/commands/{command['id']}", token=token)
    print(f"result: {command['state']} (valve now {command['result_state']})")
    if command["state"] != "succeeded":
        sys.exit(1)


if __name__ == "__main__":
    main()
