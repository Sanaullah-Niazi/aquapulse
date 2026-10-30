"""Command line: `aquapulse-sim run | profile | devices | scenario | send ...`.

`run` starts the simulator. The other commands are small helpers:
* `profile` prints a week of simulated water use, so you can see the
  morning/evening peaks and weekend effect without any Docker or MQTT;
* `devices` and `scenario` talk to a running simulator's control API;
* `send` publishes a command over MQTT, like the backend will in M4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .config import Config, ConfigError


def _api(method: str, path: str, body: dict | None = None):
    base = os.environ.get("SIM_API_URL", "http://localhost:8090")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        sys.exit(f"Error {err.code}: {err.read().decode()}")
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach the simulator at {base} ({err.reason}). Is it running? make up")


def cmd_run(_: argparse.Namespace) -> None:
    from .app import run

    try:
        cfg = Config.from_env()
    except ConfigError as err:
        sys.exit(f"Configuration error: {err}")
    asyncio.run(run(cfg))


def cmd_profile(args: argparse.Namespace) -> None:
    """Print daily litres per home and the average hourly pattern."""
    from .building import Building
    from .profile import HouseholdProfile
    from .store import FlashStore

    cfg = Config.from_env()
    # Same homes as the running twin. Memory-only store: nothing is read or written.
    building = Building(cfg, 0.0, FlashStore(None))
    start = datetime.now(cfg.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [start.date() - timedelta(days=args.days - i) for i in range(args.days)]
    print(f"Seed {cfg.seed}, time zone {cfg.timezone}, {args.days} days\n")
    print(f"{'device':<14}{'people':>7}{'L/day':>9}{'L/person/day':>15}")
    hourly = [0.0] * 24
    for home in building.homes:
        profile: HouseholdProfile = home.profile
        total = 0.0
        for day in days:
            midnight = datetime.combine(day, datetime.min.time(), cfg.tz).timestamp()
            for hour in range(24):
                t0 = midnight + hour * 3600
                litres = profile.litres_between(t0, t0 + 3600)
                hourly[hour] += litres
                total += litres
        per_day = total / args.days
        print(f"{home.id:<14}{home.persons:>7}{per_day:>9.0f}{per_day / home.persons:>15.1f}")
    print("\nWhole building, average litres per hour of the day:")
    peak = max(hourly)
    for hour, litres in enumerate(hourly):
        bar = "#" * round(40 * litres / peak)
        print(f"  {hour:02d}:00 {litres / args.days:7.0f} {bar}")


def cmd_backfill(args: argparse.Namespace) -> None:
    """Print N days of history as JSON lines (FR-08). Progress goes to stderr."""
    import time

    from .backfill import write

    cfg = Config.from_env()
    started = time.monotonic()
    count = write(cfg, args.days, time.time())
    print(
        f"backfill: {count} readings for {args.days} days in {time.monotonic() - started:.0f} s",
        file=sys.stderr,
    )


def cmd_devices(_: argparse.Namespace) -> None:
    for d in _api("GET", "/devices"):
        print(json.dumps(d))


def cmd_scenario(args: argparse.Namespace) -> None:
    params = json.loads(args.params) if args.params else {}
    result = _api(
        "POST",
        "/scenarios",
        {"type": args.type, "device": args.device, "minutes": args.minutes, "params": params},
    )
    print(json.dumps(result, indent=2))


def cmd_send(args: argparse.Namespace) -> None:
    """Publish one command like the backend will (M4), and print the device's ack."""
    import time
    import uuid

    import aiomqtt

    from . import contract

    now = time.time()
    cmd: dict = {
        "v": 1,
        "cmdId": str(uuid.uuid4()),
        "type": args.type,
        "issuedAt": contract.iso(now),
        "expiresAt": contract.iso(now + 30),
        "by": "owner",
    }
    if args.type == "thresholds":
        low, high = (float(x) for x in args.value.split(","))
        cmd |= {"lowPct": low, "highPct": high}
    elif args.type == "interval":
        cmd["seconds"] = int(args.value)
    else:
        cmd["action"] = args.value
    problems = contract.validate("cmd.v1", cmd)
    if problems:
        sys.exit("Not a valid command: " + "; ".join(problems))

    async def send() -> dict | None:
        host = os.environ.get("MQTT_HOST", "localhost")
        port = int(os.environ.get("MQTT_PORT", "1883"))
        async with aiomqtt.Client(host, port, identifier=f"cli-{cmd['cmdId'][:8]}") as client:
            await client.subscribe(contract.topic(args.device, "ack"), qos=1)
            await client.publish(contract.topic(args.device, "cmd"), json.dumps(cmd), qos=1)
            print("sent:", json.dumps(cmd))
            try:
                async with asyncio.timeout(15):
                    async for message in client.messages:
                        ack = json.loads(message.payload)
                        if ack.get("cmdId") == cmd["cmdId"]:
                            return ack
            except TimeoutError:
                return None
        return None

    ack = asyncio.run(send())
    if ack is None:
        sys.exit("No ack within 15 s. Is the device online? (make sim-logs)")
    print("ack: ", json.dumps(ack))


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SIM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="aquapulse-sim", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="start the digital twin").set_defaults(func=cmd_run)
    p = sub.add_parser("profile", help="print a week of simulated water use (no MQTT needed)")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_profile)
    p = sub.add_parser("backfill", help="print N days of history as JSON lines (no MQTT)")
    p.add_argument("--days", type=int, default=90, choices=range(1, 366), metavar="1..365")
    p.set_defaults(func=cmd_backfill)
    sub.add_parser("devices", help="list devices of the running simulator").set_defaults(
        func=cmd_devices
    )
    p = sub.add_parser("scenario", help="inject a scenario into the running simulator")
    p.add_argument(
        "type",
        help="leak, burst, ph_drift, temp_spike, sensor_stuck, valve_stuck, "
        "offline, reboot, tank_sensor_fail, sump_empty",
    )
    p.add_argument("device", help="e.g. home-b01-03 or tank-b01-01")
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--params", help="JSON, e.g. '{\"lpm\": 1.5}'")
    p.set_defaults(func=cmd_scenario)
    p = sub.add_parser("send", help="send a command over MQTT and wait for the ack")
    p.add_argument("device", help="e.g. home-b01-03 or tank-b01-01")
    p.add_argument("type", choices=["valve", "pump", "mode", "thresholds", "interval"])
    p.add_argument(
        "value",
        help="valve: open|close · pump: on|off · mode: auto|manual · "
        "thresholds: LOW,HIGH (e.g. 30,90) · interval: seconds",
    )
    p.set_defaults(func=cmd_send)
    args = parser.parse_args()
    args.func(args)
