"""The MQTT device contract (SRS section 7.3), server side.

The JSON Schemas in /contracts are the single source of truth. The simulator
checks every message before sending; the ingestor checks every message it
receives with the very same files (FR-22). A real ESP32 must pass the same
checks, so it can replace a simulated device without changes on the server.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

TOPIC_ROOT = "aquapulse/v1/dev"
CHANNELS = ("telemetry", "status", "state", "cmd", "ack")
MAX_PAYLOAD_BYTES = 2048  # FR-10: payloads are at most 2 KB

SCHEMA_FOR = {
    ("home", "telemetry"): "telemetry-home.v1",
    ("tank", "telemetry"): "telemetry-tank.v1",
    ("home", "state"): "state-home.v1",
    ("tank", "state"): "state-tank.v1",
    ("home", "status"): "status.v1",
    ("tank", "status"): "status.v1",
    ("home", "ack"): "ack.v1",
    ("tank", "ack"): "ack.v1",
    ("home", "cmd"): "cmd.v1",
    ("tank", "cmd"): "cmd.v1",
}


class ContractError(ValueError):
    """A message does not follow the contract. `reason` is a short label for metrics."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def contracts_dir() -> Path:
    """/contracts: from AQUAPULSE_CONTRACTS_DIR in Docker, else found next to the repo."""
    env = os.environ.get("AQUAPULSE_CONTRACTS_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if (parent / "contracts" / "schemas").is_dir():
            return parent / "contracts"
    raise FileNotFoundError("contracts/schemas not found; set AQUAPULSE_CONTRACTS_DIR")


@cache
def _validators() -> dict[str, Draft202012Validator]:
    schema_dir = contracts_dir() / "schemas"
    by_file = {p.name: json.loads(p.read_text()) for p in sorted(schema_dir.glob("*.json"))}
    return {
        filename.removesuffix(".json"): Draft202012Validator(_inline_refs(schema, schema, by_file))
        for filename, schema in by_file.items()
    }


def _inline_refs(node: Any, doc: dict, by_file: dict[str, dict]) -> Any:
    """Replace every "$ref" with a copy of what it points to.

    Following a $ref at validation time is slow in the jsonschema library
    (about 0.7 ms per message). Our schemas only point into "$defs", never
    in a loop, so we resolve them once at start-up and validation gets
    ~10x faster. The rules themselves do not change."""
    if isinstance(node, list):
        return [_inline_refs(item, doc, by_file) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        filename, _, pointer = node["$ref"].partition("#")
        target_doc = by_file[filename] if filename else doc
        target: Any = target_doc
        for part in filter(None, pointer.split("/")):
            target = target[part]
        rest = {k: v for k, v in node.items() if k != "$ref"}
        return {
            **_inline_refs(target, target_doc, by_file),
            **_inline_refs(rest, doc, by_file),
        }
    return {k: _inline_refs(v, doc, by_file) for k, v in node.items()}


def parse_topic(topic: str) -> tuple[str, str]:
    """'aquapulse/v1/dev/home-b01-03/telemetry' -> ('home-b01-03', 'telemetry')."""
    parts = topic.split("/")
    if len(parts) != 5 or "/".join(parts[:3]) != TOPIC_ROOT or parts[4] not in CHANNELS:
        raise ContractError("bad_topic")
    return parts[3], parts[4]


def topic(device_id: str, channel: str) -> str:
    return f"{TOPIC_ROOT}/{device_id}/{channel}"


def device_kind(device_id: str) -> str:
    return device_id.split("-", 1)[0]


def decode(device_id: str, channel: str, raw: bytes) -> dict[str, Any]:
    """Size check, JSON parse and schema check. Raises ContractError."""
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ContractError("too_large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ContractError("not_json") from None
    schema = SCHEMA_FOR.get((device_kind(device_id), channel))
    if schema is None:
        raise ContractError("bad_topic")
    errors = list(_validators()[schema].iter_errors(payload))
    if errors:
        # Only the field path goes into the error, never the values (they could be anything).
        raise ContractError("schema", "/".join(map(str, errors[0].absolute_path)) or "(root)")
    return payload


def encode(device_id: str, channel: str, payload: dict[str, Any]) -> bytes:
    """Check an outgoing message (commands) against its schema, then serialise it."""
    errors = list(_validators()[SCHEMA_FOR[(device_kind(device_id), channel)]].iter_errors(payload))
    if errors:
        raise ContractError("schema", errors[0].message)
    return json.dumps(payload, separators=(",", ":")).encode()


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
