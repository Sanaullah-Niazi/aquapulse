"""Simulator settings, read from environment variables (see .env.example).

Every setting has a safe default, so `aquapulse-sim run` works with no setup.
Invalid values stop the simulator at start-up with a clear message, instead of
producing strange data later.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ConfigError(ValueError):
    """A setting has an invalid value."""


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _float(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


def parse_weekend(raw: str) -> frozenset[int]:
    """'sat,sun' -> {5, 6} (Monday is 0, like Python's date.weekday())."""
    days = set()
    for part in raw.lower().replace(" ", "").split(","):
        if part not in WEEKDAY_NAMES:
            raise ConfigError(f"SIM_WEEKEND_DAYS: unknown day {part!r}, use mon..sun")
        days.add(WEEKDAY_NAMES.index(part))
    return frozenset(days)


@dataclass(frozen=True)
class Config:
    homes: int = 10  # number of virtual home nodes (FR-01: 1 to 500)
    interval_s: int = 10  # seconds between telemetry messages (FR-03: 1 to 300)
    seed: int = 42  # same seed = exactly the same water use (FR-02)
    building: str = "b01"  # used in device IDs: home-b01-01, tank-b01-01
    timezone: str = "Asia/Karachi"  # local time drives the morning/evening peaks
    weekend: frozenset[int] = field(default_factory=lambda: frozenset({5, 6}))
    tank_capacity_l: float = 5000.0
    tank_start_pct: float = 30.0
    pump_lpm: float = 60.0
    mqtt_host: str = "localhost"
    mqtt_port: int = 8883  # TLS only since M5 (FR-12)
    mqtt_ca_file: str = "/certs/ca.crt"  # our project CA: the broker's certificate is checked
    device_secret: str = ""  # SIM_DEVICE_SECRET: each device's password is made from it
    api_host: str = "0.0.0.0"  # inside the container; Docker publishes it on 127.0.0.1 only
    api_port: int = 8090
    data_dir: str | None = None  # where counters are saved ("flash"); None = memory only
    fw: str = "1.0.0"  # firmware version the virtual devices report

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls) -> Config:
        timezone = os.environ.get("DEMO_TIMEZONE", "Asia/Karachi")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"DEMO_TIMEZONE: unknown time zone {timezone!r}") from None
        building = os.environ.get("SIM_BUILDING", "b01").lower()
        if not (building.isalnum() and 1 <= len(building) <= 16):
            raise ConfigError("SIM_BUILDING must be 1 to 16 letters or digits, e.g. b01")
        return cls(
            homes=_int("SIM_HOMES", 10, 1, 500),
            interval_s=_int("SIM_INTERVAL_S", 10, 1, 300),
            seed=_int("SIM_SEED", 42, 0, 2**31 - 1),
            building=building,
            timezone=timezone,
            weekend=parse_weekend(os.environ.get("SIM_WEEKEND_DAYS", "sat,sun")),
            tank_capacity_l=_float("SIM_TANK_CAPACITY_L", 5000, 500, 100_000),
            tank_start_pct=_float("SIM_TANK_START_PCT", 30, 0, 100),
            pump_lpm=_float("SIM_PUMP_LPM", 60, 5, 1000),
            mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
            mqtt_port=_int("MQTT_PORT", 8883, 1, 65535),
            mqtt_ca_file=os.environ.get("MQTT_CA_FILE", "/certs/ca.crt"),
            device_secret=os.environ.get("SIM_DEVICE_SECRET", ""),
            api_host=os.environ.get("SIM_API_HOST", "0.0.0.0"),
            api_port=_int("SIM_API_PORT", 8090, 1, 65535),
            data_dir=os.environ.get("SIM_DATA_DIR") or None,
        )


def device_password(secret: str, device_id: str) -> str:
    """A virtual device's MQTT password (M5): HMAC-SHA256 of its ID with SIM_DEVICE_SECRET.

    The backend's broker-auth service computes the same value (and stores only a
    hash of it in the broker's password list), so nobody has to copy 11
    passwords around. Real devices get a random password when registered.
    Must give the same result as app.security.mqtt_passwords.simulator_password.
    """
    digest = hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
