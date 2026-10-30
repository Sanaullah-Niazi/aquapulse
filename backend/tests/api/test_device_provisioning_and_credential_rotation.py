"""FR-14: device registration, password rotation and revoke (API side).

That the broker really accepts and refuses these passwords within seconds is
proved against the running stack by `make smoke` (and tests/broker).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from sqlalchemy import text

from app import db
from app import events as bus
from app.models import Device
from app.security.mqtt_passwords import DEVICE_ID, verify

from ..conftest import audit_actions


@pytest.fixture
async def world(make):
    return await make.world()


async def _register(client, make, world, path=None):
    path = path or f"/api/v1/homes/{world.home.id}/device"
    return await client.post(path, headers=await make.headers(world.owner))


async def _saved(device_id: str) -> Device:
    async with db.sessions()() as session:
        device = await session.get(Device, device_id)
        assert device is not None
        return device


async def test_register_shows_the_password_once_and_stores_only_a_hash(client, make, world):
    r = await _register(client, make, world)
    assert r.status_code == 201, r.text
    body = r.json()
    device_id, password = body["device"]["id"], body["password"]
    assert DEVICE_ID.match(device_id) and body["username"] == device_id
    assert len(password) == 43  # 32 random bytes
    assert body["device"]["has_own_password"] is True
    assert "mqtt_password_hash" not in body["device"]
    saved = await _saved(device_id)
    assert saved.home_id == world.home.id and saved.status == "active"
    assert saved.mqtt_password_hash.startswith("$7$") and verify(password, saved.mqtt_password_hash)
    assert "device.registered" in await audit_actions(target_id=device_id)


async def test_the_qr_code_holds_the_whole_device_config(client, make, world):
    body = (await _register(client, make, world)).json()
    config = body["config"]
    assert config["deviceId"] == body["username"]
    assert config["mqtt"] == {
        "host": "localhost",
        "port": 8883,
        "tls": True,
        "username": body["username"],
        "password": body["password"],
        "caSha256": body["broker"]["ca_sha256"],
    }
    assert body["broker"]["ca_sha256"].count(":") == 31  # SHA-256 of the project CA
    assert body["qr_svg"].startswith("data:image/svg+xml")


async def test_plain_password_is_never_stored_or_logged(client, make, world, caplog):
    """FR-14 criterion 2: search the whole database and the logs for it."""
    with caplog.at_level(logging.DEBUG):
        first = (await _register(client, make, world)).json()
        second = (
            await client.post(
                f"/api/v1/devices/{first['username']}/password",
                headers=await make.headers(world.owner),
            )
        ).json()
    for password in (first["password"], second["password"]):
        assert password not in caplog.text
        async with db.sessions()() as session:
            # Every text-like column of every table, as text.
            tables = (
                await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                        " AND has_table_privilege(format('%I.%I', schemaname, tablename), 'SELECT')"
                    )
                )
            ).scalars()
            for table in tables:
                found = await session.scalar(
                    text(f"SELECT count(*) FROM {table} t WHERE t::text LIKE :p"),  # noqa: S608
                    {"p": f"%{password}%"},
                )
                assert found == 0, f"password found in {table}"


async def test_a_home_has_only_one_active_device(client, make, world):
    assert (await _register(client, make, world)).status_code == 201
    r = await _register(client, make, world)
    assert r.status_code == 409 and "Revoke it first" in r.json()["detail"]


async def test_rotation_replaces_the_password(client, make, world):
    first = (await _register(client, make, world)).json()
    headers = await make.headers(world.owner)
    r = await client.post(f"/api/v1/devices/{first['username']}/password", headers=headers)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["username"] == first["username"] and second["password"] != first["password"]
    saved = await _saved(first["username"])
    assert verify(second["password"], saved.mqtt_password_hash)
    assert not verify(first["password"], saved.mqtt_password_hash)  # old one is dead
    assert "device.password_rotated" in await audit_actions(target_id=first["username"])


async def test_revoke_switches_the_device_off_and_allows_a_new_one(client, make, world):
    first = (await _register(client, make, world)).json()
    device_id = first["username"]
    headers = await make.headers(world.owner)
    r = await client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    saved = await _saved(device_id)
    assert saved.mqtt_password_hash is None and saved.revoked_at is not None
    # Revoking twice is fine, rotating a revoked device is not.
    assert (
        await client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
    ).status_code == 200
    r = await client.post(f"/api/v1/devices/{device_id}/password", headers=headers)
    assert r.status_code == 409
    assert (await audit_actions(target_id=device_id)).count("device.revoked") == 1
    # A replacement device for the same home.
    replacement = await _register(client, make, world)
    assert replacement.status_code == 201
    assert replacement.json()["username"] != device_id
    # Live data now follows the new device, not the revoked one.
    live = await client.get(f"/api/v1/homes/{world.home.id}/live", headers=headers)
    assert live.json()["device_id"] == replacement.json()["username"]


async def test_tank_device_and_device_list(client, make, world):
    tank = await make.tank(world.building)
    r = await _register(client, make, world, f"/api/v1/tanks/{tank.id}/device")
    assert r.status_code == 201 and r.json()["device"]["kind"] == "tank"
    simulated = await make.device(world.home)  # like the demo devices: no own password
    listed = await client.get(
        f"/api/v1/buildings/{world.building.id}/devices", headers=await make.headers(world.owner)
    )
    by_id = {d["id"]: d for d in listed.json()}
    assert by_id[r.json()["username"]]["has_own_password"] is True
    assert by_id[simulated.id]["has_own_password"] is False


async def test_archived_home_gets_no_device(client, make, world):
    headers = await make.headers(world.owner)
    await client.delete(f"/api/v1/homes/{world.home.id}", headers=headers)
    assert (await _register(client, make, world)).status_code == 409


async def test_another_owners_devices_are_invisible(client, make, world):
    device_id = (await _register(client, make, world)).json()["username"]
    stranger = await make.user("owner")
    headers = await make.headers(stranger)
    for path in (f"/api/v1/devices/{device_id}/password", f"/api/v1/devices/{device_id}/revoke"):
        assert (await client.post(path, headers=headers)).status_code == 404
    assert (
        await client.post("/api/v1/devices/home-nope-01/revoke", headers=headers)
    ).status_code == 404
    other = await client.post(f"/api/v1/homes/{world.home.id}/device", headers=headers)
    assert other.status_code == 404
    tank = await make.tank(world.building)
    assert (
        await client.post(f"/api/v1/tanks/{tank.id}/device", headers=headers)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/tanks/{world.home.id}/device", headers=headers)
    ).status_code == 404


async def test_every_change_tells_broker_auth(client, make, world):
    """The API NOTIFYs aquapulse_devices, so the broker learns about it in about a second."""
    heard: list[str] = []
    async with bus.listening(bus.DEVICES, heard.append):
        device_id = (await _register(client, make, world)).json()["username"]
        headers = await make.headers(world.owner)
        await client.post(f"/api/v1/devices/{device_id}/password", headers=headers)
        await client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
        for _ in range(50):
            if len(heard) >= 3:
                break
            await asyncio.sleep(0.05)
    assert heard == [device_id] * 3
    assert all(json.dumps(h) for h in heard)
