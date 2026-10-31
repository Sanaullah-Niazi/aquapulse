"""Command line: `aquapulse-sim run | profile | backfill | devices | scenario ...`.

`run` starts the simulator. The other commands are small helpers:
* `profile` prints a week of simulated water use, so you can see the
  morning/evening peaks and weekend effect without any Docker or MQTT;
* `devices` and `scenario` talk to a running simulator's control API.

(The `send` command of M2 to M4 is gone: since M5 only the server's
`commander` MQTT account may send commands. Use `make valve-close`, which
goes through the API, or Swagger.)
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
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


def cmd_eval_set(args: argparse.Namespace) -> None:
    """Print a labelled leak test set as JSON lines (FR-63). Fixed seed and end time."""
    import time
    from datetime import datetime

    from .evalset import EVAL_SEED, write

    cfg = dataclasses.replace(Config.from_env(), seed=EVAL_SEED, homes=args.homes)
    end = datetime(2026, 6, 1, tzinfo=cfg.tz).timestamp()  # fixed: same set every time
    started = time.monotonic()
    count = write(
        cfg, end, days=args.days, leaks=args.leaks, bursts=args.bursts, abnormal=args.abnormal
    )
    print(
        f"eval-set: {count} readings, {args.leaks} leaks, {args.bursts} bursts and "
        f"{args.abnormal} unusual days over "
        f"{args.days} days in {time.monotonic() - started:.0f} s",
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
    p = sub.add_parser("eval-set", help="print a labelled leak test set (FR-63, no MQTT)")
    p.add_argument("--homes", type=int, default=10)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--leaks", type=int, default=50)
    p.add_argument("--bursts", type=int, default=12)
    p.add_argument("--abnormal", type=int, default=30)
    p.set_defaults(func=cmd_eval_set)
    sub.add_parser("devices", help="list devices of the running simulator").set_defaults(
        func=cmd_devices
    )
    p = sub.add_parser("scenario", help="inject a scenario into the running simulator")
    p.add_argument(
        "type",
        help="leak, burst, abnormal, ph_drift, temp_spike, sensor_stuck, valve_stuck, "
        "offline, reboot, tank_sensor_fail, sump_empty",
    )
    p.add_argument("device", help="e.g. home-b01-03 or tank-b01-01")
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--params", help="JSON, e.g. '{\"lpm\": 1.5}'")
    p.set_defaults(func=cmd_scenario)
    args = parser.parse_args()
    args.func(args)
