"""broker-auth: keeps Mosquitto's password list in step with the database (M5).

Mosquitto reads `username:hash` lines from a file. This small service writes
that file into a volume shared with the broker container, whose start script
reloads the broker within a second of every change (infra/mosquitto/start.sh).

Who is in the list:

* the three server accounts (ingestor, commander, monitor), with passwords
  from .env, each allowed only its own topics by infra/mosquitto/acl (FR-11);
* every ACTIVE device in the database that has its own password (FR-14);
* the simulated devices and `home-test-01` (used by automated tests), with
  passwords made from SIM_DEVICE_SECRET, unless the database says otherwise:
  a revoked one is left out, and one that got its own password uses that.

It rewrites the list when the API NOTIFYs `aquapulse_devices` (a device was
registered, rotated or revoked) and every 30 s anyway. No plain password is
ever written: only hashes.

    python -m app.brokerauth
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import select

from . import db
from . import events as bus
from .config import Settings, get_settings
from .models import Device
from .security.mqtt_passwords import mosquitto_hash, simulator_password

log = logging.getLogger("aquapulse.brokerauth")

SERVICE_ACCOUNTS = ("ingestor", "commander", "monitor")
TEST_DEVICE = "home-test-01"
RESYNC_S = 30.0


def simulated_devices(settings: Settings) -> set[str]:
    """The device IDs the simulator runs (same rule as the simulator's config)."""
    code = settings.sim_building
    homes = {f"home-{code}-{n:02d}" for n in range(1, settings.sim_homes + 1)}
    return homes | {f"tank-{code}-01", TEST_DEVICE}


class PasswordList:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Hashed once per run with a random salt, so the file only changes
        # when an account really changes.
        self.services = {
            name: mosquitto_hash(secret.get_secret_value())
            for name, secret in (
                ("ingestor", self.settings.mqtt_ingestor_password),
                ("commander", self.settings.mqtt_commander_password),
                ("monitor", self.settings.mqtt_monitor_password),
            )
            if secret.get_secret_value()
        }
        secret = self.settings.sim_device_secret.get_secret_value()
        self.simulated = (
            {
                d: mosquitto_hash(simulator_password(secret, d))
                for d in simulated_devices(self.settings)
            }
            if secret
            else {}
        )

    async def accounts(self) -> dict[str, str]:
        """username -> hash for everyone allowed to log in right now."""
        async with db.sessions()() as session:
            rows = (
                await session.execute(select(Device.id, Device.status, Device.mqtt_password_hash))
            ).all()
        accounts = dict(self.services)
        in_database = set()
        for device_id, status, password_hash in rows:
            in_database.add(device_id)
            if status != "active":
                continue  # revoked: no account, so the broker refuses it (FR-14 crit. 3)
            if password_hash:
                accounts[device_id] = password_hash
            elif device_id in self.simulated:
                accounts[device_id] = self.simulated[device_id]
        for device_id, password_hash in self.simulated.items():
            if device_id not in in_database:  # e.g. before `make seed`
                accounts[device_id] = password_hash
        return accounts

    async def write(self, path: Path) -> bool:
        """Write the list if it changed. Returns True if the file was replaced."""
        accounts = await self.accounts()
        content = "".join(f"{user}:{h}\n" for user, h in sorted(accounts.items()))
        if path.exists() and path.read_text() == content:
            return False
        _replace(path, content)
        devices = len(accounts) - len(self.services)
        log.info(
            "password list updated: %d server accounts, %d devices", len(self.services), devices
        )
        return True


def _replace(path: Path, content: str) -> None:
    """Write to a temporary file, then rename it: the broker never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".passwd-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


async def run() -> None:
    settings = get_settings()
    missing = [
        n
        for n in SERVICE_ACCOUNTS
        if not getattr(settings, f"mqtt_{n}_password").get_secret_value()
    ]
    if missing:
        log.warning("no password in .env for: %s (run `make env`)", ", ".join(missing))
    passwords = PasswordList(settings)
    path = Path(settings.broker_auth_file)
    wake = asyncio.Event()
    pause = 1.0
    while True:
        try:
            async with bus.listening(bus.DEVICES, lambda _: wake.set()):
                pause = 1.0
                while True:
                    await passwords.write(path)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(wake.wait(), RESYNC_S)
                    wake.clear()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            log.warning("cannot read devices (%s), retrying in %.0f s", type(err).__name__, pause)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
