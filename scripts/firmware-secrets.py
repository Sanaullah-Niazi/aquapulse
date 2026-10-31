"""Write firmware/include/secrets.h for a device (M11): make firmware-secrets.

It fills in the broker address, the device's ID and password, and the
project CA from infra/certs/public/ca.crt, so you do not copy them by hand.

The password: pass the one the web app showed (Devices -> New password, or
Register) with PASSWORD=... Without it, the demo device's simulator password
is used (made from SIM_DEVICE_SECRET in .env, as the simulator and the broker
do). Then the simulator also logs in as that device, and the two keep
pushing each other off the broker, so giving the device its own password is
the normal way.

secrets.h is git-ignored: never commit it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "firmware" / "include" / "secrets.h"
CA = ROOT / "infra" / "certs" / "public" / "ca.crt"


def env_value(name: str) -> str:
    env = ROOT / ".env"
    if not env.exists():
        return ""
    for line in env.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def simulator_password(secret: str, device_id: str) -> str:
    """Same as aquapulse_sim.config.device_password and the broker's own check."""
    digest = hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def c_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--device", default="home-b01-10")
    p.add_argument("--password", default="")
    p.add_argument("--host", default="host.wokwi.internal")
    p.add_argument("--port", type=int, default=8883)
    p.add_argument("--wifi-ssid", default="Wokwi-GUEST")
    p.add_argument("--wifi-password", default="")
    a = p.parse_args()

    if not CA.exists():
        print(
            "infra/certs/public/ca.crt is missing: run make certs first",
            file=sys.stderr,
        )
        return 1
    password = a.password
    if not password:
        secret = env_value("SIM_DEVICE_SECRET")
        if not secret:
            print(
                "No PASSWORD= given and SIM_DEVICE_SECRET is not in .env (run make env)",
                file=sys.stderr,
            )
            return 1
        password = simulator_password(secret, a.device)

    ca_lines = [line for line in CA.read_text().splitlines() if line.strip()]
    ca = " \\\n".join(f'  "{line}\\n"' for line in ca_lines)
    OUT.write_text(
        "// Made by make firmware-secrets. Git-ignored: never commit this file.\n"
        "#pragma once\n\n"
        f"#define WIFI_SSID {c_string(a.wifi_ssid)}\n"
        f"#define WIFI_PASSWORD {c_string(a.wifi_password)}\n\n"
        f"#define MQTT_HOST {c_string(a.host)}\n"
        f"#define MQTT_PORT {a.port}\n\n"
        f"#define DEVICE_ID {c_string(a.device)}\n"
        f"#define DEVICE_PASSWORD {c_string(password)}\n\n"
        f"#define CA_CERT \\\n{ca}\n"
    )
    print(
        f"Wrote {OUT.relative_to(ROOT)} for {a.device} (broker {a.host}:{a.port}, Wi-Fi {a.wifi_ssid})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
