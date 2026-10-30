"""What every virtual device has in common: identity, boot, commands, outbox.

A device never talks to the network itself. It puts the messages it wants to
send into its *outbox*, and the MQTT link (mqtt_link.py) sends them. That
keeps all the logic here testable with a fake clock and no broker.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from . import contract
from .config import Config
from .scenarios import ScenarioBook
from .store import SAVE_EVERY_LITRES, SAVE_EVERY_SECONDS, FlashStore

SEEN_COMMANDS_KEPT = 256  # remember this many command IDs to drop duplicates


@dataclass(frozen=True)
class Outgoing:
    channel: str  # "state" or "ack" (telemetry and status are sent by the link)
    payload: dict[str, Any]
    qos: int
    retain: bool


class Device:
    kind = "device"

    def __init__(
        self, device_id: str, cfg: Config, scenarios: ScenarioBook, store: FlashStore, now: float
    ) -> None:
        self.id = device_id
        self.cfg = cfg
        self.scenarios = scenarios
        self.store = store
        self.interval_s = cfg.interval_s
        self.outbox: deque[Outgoing] = deque()
        self.outbox_ready = asyncio.Event()  # wakes the MQTT link when something is queued
        self.stopped = False  # switched off by hand via the control API
        self.connected = False  # set by the MQTT link
        self.reboot_count = 0
        self._seen: deque[str] = deque(maxlen=SEEN_COMMANDS_KEPT)
        self._saved_at = now
        self._boot(now)

    # ----- boot and reboot (FR-03, FR-17) -----

    def _boot(self, now: float) -> None:
        # Truly random (not from the seed): the server must see a new bootId after
        # every restart, including a restart of the whole simulator.
        self.boot_id = str(uuid.uuid4())
        self.seq = 0
        self.boot_pending = True  # the next telemetry message carries "boot": true
        self._seen.clear()  # RAM is empty after a restart
        self.restore(now)

    def reboot(self, now: float) -> None:
        """Simulated power cut + restart. The MQTT link notices and reconnects."""
        self.reboot_count += 1
        self._boot(now)

    def restore(self, now: float) -> None:
        """Load saved values from flash. Implemented by HomeNode and TankNode."""

    def save(self, now: float) -> None:
        """Write values to flash. Implemented by HomeNode and TankNode."""

    def maybe_save(self, now: float, changed_litres: float = 0.0) -> None:
        if changed_litres >= SAVE_EVERY_LITRES or now - self._saved_at >= SAVE_EVERY_SECONDS:
            self.save(now)
            self._saved_at = now

    # ----- telemetry -----

    def header(self, now: float) -> dict[str, Any]:
        """Fields at the start of every telemetry message. Advances seq."""
        msg = {"v": 1, "ts": contract.iso(now), "bootId": self.boot_id, "seq": self.seq}
        self.seq += 1
        return msg

    def take_boot_flag(self) -> bool:
        flag, self.boot_pending = self.boot_pending, False
        return flag

    def rssi(self, now: float) -> int:
        base = random.Random(f"{self.cfg.seed}:{self.id}:rssi").randint(-72, -48)
        return base + random.Random(f"{self.cfg.seed}:{self.id}:rssi:{int(now)}").randint(-3, 3)

    def telemetry(self, now: float) -> dict[str, Any]:
        raise NotImplementedError

    def state(self, now: float) -> dict[str, Any]:
        raise NotImplementedError

    # ----- outbox -----

    def queue(self, channel: str, payload: dict[str, Any], qos: int = 1, retain: bool = False):
        self.outbox.append(Outgoing(channel, payload, qos, retain))
        self.outbox_ready.set()

    def queue_state(self, now: float) -> None:
        self.queue("state", self.state(now), qos=1, retain=True)

    def ack(
        self, cmd_id: str | None, result: str, reason: str | None, state: str | None, now: float
    ) -> None:
        self.queue(
            "ack",
            {
                "v": 1,
                "cmdId": cmd_id,
                "result": result,
                "reason": reason,
                "state": state,
                "ts": contract.iso(now),
            },
        )

    # ----- commands (FR-04, FR-13; SRS diagram 10.5) -----

    def handle_command(self, raw: bytes, now: float) -> None:
        try:
            cmd = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            cmd = None
        cmd_id = None
        if isinstance(cmd, dict) and contract.is_uuid(cmd.get("cmdId")):
            cmd_id = cmd["cmdId"]  # a readable ID, so the server can match our answer

        if cmd is None or len(raw) > contract.MAX_PAYLOAD_BYTES or contract.validate("cmd.v1", cmd):
            self.ack(cmd_id, "rejected", "INVALID", None, now)
            return
        if contract.parse_iso(cmd["expiresAt"]) <= now:
            self.ack(cmd_id, "rejected", "EXPIRED", None, now)
            return
        if cmd_id in self._seen:
            # Same command delivered twice (QoS 1 can do that): never act twice.
            self.ack(cmd_id, "rejected", "DUPLICATE", None, now)
            return
        self._seen.append(cmd_id)

        if cmd["type"] == "interval":
            self.interval_s = cmd["seconds"]
            self.ack(cmd_id, "ok", None, None, now)
            self.queue_state(now)
            return
        self.execute(cmd, now)

    def execute(self, cmd: dict[str, Any], now: float) -> None:
        """Device-specific commands. Implemented by HomeNode and TankNode."""
        self.ack(cmd["cmdId"], "rejected", "UNSUPPORTED", None, now)
