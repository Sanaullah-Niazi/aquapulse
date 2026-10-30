"""The devices' "flash memory" (FR-17).

A real ESP32 keeps its litre counter in RAM and copies it to flash now and
then. Flash wears out if written too often, so it is saved only after 10 L
more water, or after 60 s if anything changed. After a reboot, the device
starts again from the last saved value, so at most 10 L can be lost.

Here "flash" is one small JSON file per device in /data (a Docker volume),
so counters also survive `make down` / `make up`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SAVE_EVERY_LITRES = 10.0
SAVE_EVERY_SECONDS = 60.0


class FlashStore:
    def __init__(self, directory: str | None) -> None:
        self._dir = Path(directory) if directory else None
        self._memory: dict[str, dict[str, Any]] = {}  # used when no directory (tests)
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, device_id: str) -> dict[str, Any] | None:
        if self._dir is None:
            data = self._memory.get(device_id)
            return dict(data) if data else None
        path = self._dir / f"{device_id}.json"
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None  # a damaged file is treated like empty flash

    def save(self, device_id: str, data: dict[str, Any]) -> None:
        if self._dir is None:
            self._memory[device_id] = dict(data)
            return
        # Write to a temporary file, then rename: a crash mid-write never
        # leaves a half-written file (rename is atomic).
        path = self._dir / f"{device_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
