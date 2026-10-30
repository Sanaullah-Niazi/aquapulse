"""broker-auth: the broker's password list follows the database (FR-11, FR-14)."""

from __future__ import annotations

import asyncio
import contextlib
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from app import brokerauth, db
from app import events as bus
from app.config import get_settings
from app.security.mqtt_passwords import mosquitto_hash, simulator_password, verify

SECRET = "test-sim-secret"


@pytest.fixture
def settings(tmp_path):
    return get_settings().model_copy(
        update={
            "mqtt_ingestor_password": SecretStr("ingest-pw"),
            "mqtt_commander_password": SecretStr("command-pw"),
            "mqtt_monitor_password": SecretStr("monitor-pw"),
            "sim_device_secret": SecretStr(SECRET),
            "sim_building": "zz9",
            "sim_homes": 2,
            "broker_auth_file": str(tmp_path / "passwd"),
        }
    )


async def test_server_accounts_and_simulated_devices_are_listed(settings):
    accounts = await brokerauth.PasswordList(settings).accounts()
    for user, password in [("ingestor", "ingest-pw"), ("commander", "command-pw")]:
        assert verify(password, accounts[user])
    for device in ("home-zz9-01", "home-zz9-02", "tank-zz9-01", "home-test-01"):
        assert verify(simulator_password(SECRET, device), accounts[device])
    assert "home-zz9-03" not in accounts  # only SIM_HOMES homes


async def test_without_secrets_there_are_no_such_accounts(settings):
    empty = settings.model_copy(
        update={"sim_device_secret": SecretStr(""), "mqtt_monitor_password": SecretStr("")}
    )
    accounts = await brokerauth.PasswordList(empty).accounts()
    assert "monitor" not in accounts and "home-test-01" not in accounts


async def test_the_database_decides_for_registered_devices(settings, make):
    world = await make.world()
    own = await make.device(world.home, mqtt_password_hash=mosquitto_hash("own-pw"))
    revoked = await make.device(world.home, status="revoked", mqtt_password_hash=None)
    no_password = await make.device(await make.home(world.building), mqtt_password_hash=None)
    # A simulated device that got its own password, and one that was revoked.
    rotated = await make.device(
        await make.home(world.building),
        device_id="home-zz9-01",
        mqtt_password_hash=mosquitto_hash("rotated-pw"),
    )
    gone = await make.device(
        await make.home(world.building), device_id="home-zz9-02", status="revoked"
    )
    accounts = await brokerauth.PasswordList(settings).accounts()
    assert verify("own-pw", accounts[own.id])
    assert revoked.id not in accounts and no_password.id not in accounts
    assert verify("rotated-pw", accounts[rotated.id])
    assert not verify(simulator_password(SECRET, rotated.id), accounts[rotated.id])
    assert gone.id not in accounts


async def test_file_is_written_only_when_something_changed(settings, make, tmp_path):
    passwords = brokerauth.PasswordList(settings)
    path = tmp_path / "passwd"
    assert await passwords.write(path) is True
    assert await passwords.write(path) is False  # nothing new: the broker is not reloaded
    content = path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "ingest-pw" not in content and SECRET not in content  # hashes only
    assert all(line.count(":") == 1 and "$7$" in line for line in content.splitlines())
    world = await make.world()
    await make.device(world.home, mqtt_password_hash=mosquitto_hash("new-pw"))
    assert await passwords.write(path) is True


async def test_service_rewrites_the_list_when_the_api_says_so(settings, make, monkeypatch):
    monkeypatch.setattr(brokerauth, "get_settings", lambda: settings)
    path = settings.broker_auth_file
    task = asyncio.create_task(brokerauth.run())
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            with contextlib.suppress(FileNotFoundError):
                if Path(path).read_text():
                    break
        world = await make.world()
        device = await make.device(world.home, mqtt_password_hash=mosquitto_hash("pw"))
        async with db.sessions()() as session, session.begin():
            await bus.notify(session, bus.DEVICES, device.id)
        for _ in range(100):
            await asyncio.sleep(0.05)
            if device.id in Path(path).read_text():
                break
        assert device.id in Path(path).read_text()  # well within FR-14's 10 seconds
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
