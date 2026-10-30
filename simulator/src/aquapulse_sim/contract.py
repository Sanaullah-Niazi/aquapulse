"""The MQTT device contract (SRS section 7.3) in code.

Topic names and payload rules live here and nowhere else. The JSON Schemas in
/contracts are the single source of truth: the simulator checks every message
against them before sending, and the backend (M4) and firmware tests (M11)
use the same files. So a simulated device and a real ESP32 are interchangeable.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

TOPIC_ROOT = "aquapulse/v1/dev"
CHANNELS = ("telemetry", "status", "state", "cmd", "ack")
MAX_PAYLOAD_BYTES = 2048  # FR-10: payloads are at most 2 KB

# Which schema checks which (device kind, channel).
SCHEMA_FOR = {
    ("home", "telemetry"): "telemetry-home.v1",
    ("tank", "telemetry"): "telemetry-tank.v1",
    ("home", "state"): "state-home.v1",
    ("tank", "state"): "state-tank.v1",
    ("home", "status"): "status.v1",
    ("tank", "status"): "status.v1",
    ("home", "cmd"): "cmd.v1",
    ("tank", "cmd"): "cmd.v1",
    ("home", "ack"): "ack.v1",
    ("tank", "ack"): "ack.v1",
}


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ContractError(ValueError):
    """A message does not follow the contract."""


def contracts_dir() -> Path:
    """Find /contracts: env var in Docker, otherwise walk up from this file."""
    env = os.environ.get("AQUAPULSE_CONTRACTS_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "schemas"
        if candidate.is_dir():
            return parent / "contracts"
    raise FileNotFoundError("contracts/schemas not found; set AQUAPULSE_CONTRACTS_DIR")


@cache
def _validators() -> dict[str, Draft202012Validator]:
    schema_dir = contracts_dir() / "schemas"
    schemas = {p.name: json.loads(p.read_text()) for p in sorted(schema_dir.glob("*.json"))}
    # The registry lets "common.v1.json#/$defs/..." references resolve offline.
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    validators = {}
    for filename, schema in schemas.items():
        Draft202012Validator.check_schema(schema)  # the schema itself must be valid
        validators[filename.removesuffix(".json")] = Draft202012Validator(schema, registry=registry)
    return validators


def schema_names() -> list[str]:
    return sorted(_validators())


def validate(schema: str, payload: Any) -> list[str]:
    """Return a list of problems (empty list = valid)."""
    validator = _validators()[schema]
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in validator.iter_errors(payload)
    ]


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and UUID_RE.match(value) is not None


def topic(device_id: str, channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    return f"{TOPIC_ROOT}/{device_id}/{channel}"


def parse_topic(name: str) -> tuple[str, str]:
    """'aquapulse/v1/dev/home-b01-03/telemetry' -> ('home-b01-03', 'telemetry')."""
    parts = name.split("/")
    if len(parts) != 5 or "/".join(parts[:3]) != TOPIC_ROOT or parts[4] not in CHANNELS:
        raise ContractError(f"not an AquaPulse v1 device topic: {name!r}")
    return parts[3], parts[4]


def device_kind(device_id: str) -> str:
    return device_id.split("-", 1)[0]


def encode(device_id: str, channel: str, payload: dict[str, Any]) -> bytes:
    """Validate a message against its schema and turn it into compact JSON bytes.

    Raising here (instead of sending a bad message) means a contract bug in the
    simulator shows up immediately in tests, never silently on the wire.
    """
    problems = validate(SCHEMA_FOR[(device_kind(device_id), channel)], payload)
    if problems:
        raise ContractError(f"{device_id}/{channel}: " + "; ".join(problems))
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ContractError(f"{device_id}/{channel}: payload is {len(data)} bytes (max 2048)")
    return data


def iso(epoch_s: float) -> str:
    """Epoch seconds -> '2026-10-30T08:15:20.123Z' (UTC, millisecond precision)."""
    text = datetime.fromtimestamp(epoch_s, UTC).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def parse_iso(text: str) -> float:
    """'2026-10-30T08:15:20Z' -> epoch seconds."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
