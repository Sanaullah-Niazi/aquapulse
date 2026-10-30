"""FR-24 (valve commands), FR-27 (owner lock) and FR-72 (command rate limit) via the API."""

from __future__ import annotations

import pytest

from ..conftest import audit_actions

pytestmark = pytest.mark.usefixtures("outbox")


@pytest.fixture
async def world(make):
    world = await make.world()
    world.device = await make.device(world.home)
    return world


def _valve(world) -> str:
    return f"/api/v1/homes/{world.home.id}/valve"


async def test_resident_closes_valve_command_is_queued_and_audited(client, make, world):
    r = await client.post(
        _valve(world),
        json={"action": "close", "reason": "Going on holiday"},
        headers=await make.headers(world.resident),
    )
    assert r.status_code == 202, r.text
    command = r.json()
    assert command["state"] == "queued" and command["device_id"] == world.device.id
    assert command["requested_role"] == "resident"
    # FR-24 criterion 4: who, role, home, action, reason.
    assert "device.command_requested" in await audit_actions(target_id=str(world.home.id))

    r = await client.get(
        f"/api/v1/commands/{command['id']}", headers=await make.headers(world.owner)
    )
    assert r.status_code == 200 and r.json()["action"] == "close"


async def test_other_residents_cannot_control_or_see_the_valve(client, make, world):
    stranger = await make.user("resident")
    other_home = await make.home(world.building)
    await make.occupancy(other_home, stranger)
    headers = await make.headers(stranger)
    assert (
        await client.post(_valve(world), json={"action": "close"}, headers=headers)
    ).status_code == 404
    mine = await client.post(
        _valve(world), json={"action": "close"}, headers=await make.headers(world.resident)
    )
    r = await client.get(f"/api/v1/commands/{mine.json()['id']}", headers=headers)
    assert r.status_code == 404  # not even whether it exists
    other_owner = await make.user("owner")
    r = await client.get(
        f"/api/v1/commands/{mine.json()['id']}", headers=await make.headers(other_owner)
    )
    assert r.status_code == 404


async def test_home_without_a_device_gives_a_clear_409(client, make):
    world = await make.world()
    r = await client.post(
        _valve(world), json={"action": "open"}, headers=await make.headers(world.owner)
    )
    assert r.status_code == 409 and "No device" in r.json()["detail"]


async def test_bad_action_or_extra_fields_are_422(client, make, world):
    headers = await make.headers(world.owner)
    for body in ({"action": "explode"}, {"action": "open", "force": True}, {}):
        assert (await client.post(_valve(world), json=body, headers=headers)).status_code == 422


async def test_eleventh_command_in_a_minute_is_429(client, make, world):
    """FR-72 criterion 2."""
    headers = await make.headers(world.resident)
    codes = [
        (await client.post(_valve(world), json={"action": "close"}, headers=headers)).status_code
        for _ in range(11)
    ]
    assert codes == [202] * 10 + [429]


# ----- locks (FR-27) -----


async def test_lock_closes_valve_blocks_everyone_and_tells_the_resident(
    client, make, world, outbox
):
    owner = await make.headers(world.owner)
    r = await client.post(
        f"/api/v1/homes/{world.home.id}/valve-lock",
        json={"state": "closed", "reason": "Plumber is fixing the kitchen pipe"},
        headers=owner,
    )
    assert r.status_code == 201, r.text
    assert r.json()["locked_state"] == "closed"

    # The valve is sent to the locked position.
    live = (await client.get(f"/api/v1/homes/{world.home.id}/live", headers=owner)).json()
    assert live["lock"]["reason"] == "Plumber is fixing the kitchen pipe"
    assert live["pending_command"]["action"] == "close"

    # FR-27 criterion 1: 423 with the reason, for the resident (and the owner too).
    for headers in (await make.headers(world.resident), owner):
        r = await client.post(_valve(world), json={"action": "open"}, headers=headers)
        assert r.status_code == 423 and "kitchen pipe" in r.json()["detail"]

    # FR-27 criterion 3: the resident is told at once.
    assert [to for to, *_ in outbox] == [world.resident.email]
    assert "kitchen pipe" in outbox[0][2]
    assert "valve.locked" in await audit_actions(target_id=str(world.home.id))

    # Unlock: the resident can use the valve again.
    r = await client.post(
        f"/api/v1/homes/{world.home.id}/valve-unlock",
        json={"reason": "Repair finished, pipe tested"},
        headers=owner,
    )
    assert r.status_code == 200 and r.json()["unlock_reason"] == "Repair finished, pipe tested"
    assert len(outbox) == 2
    r = await client.post(
        _valve(world), json={"action": "open"}, headers=await make.headers(world.resident)
    )
    assert r.status_code == 202
    assert "valve.unlocked" in await audit_actions(target_id=str(world.home.id))


@pytest.mark.parametrize("reason", ["", "too short", "         x         "])
async def test_lock_needs_a_real_reason(client, make, world, reason):
    """FR-27 criterion 2: at least 10 characters."""
    r = await client.post(
        f"/api/v1/homes/{world.home.id}/valve-lock",
        json={"state": "closed", "reason": reason},
        headers=await make.headers(world.owner),
    )
    assert r.status_code == 422


async def test_lock_twice_or_unlock_when_not_locked_is_409(client, make, world):
    owner = await make.headers(world.owner)
    lock = {"state": "open", "reason": "Supply must stay on today"}
    url = f"/api/v1/homes/{world.home.id}"
    r = await client.post(
        f"{url}/valve-unlock", json={"reason": "Nothing to unlock"}, headers=owner
    )
    assert r.status_code == 409
    assert (await client.post(f"{url}/valve-lock", json=lock, headers=owner)).status_code == 201
    assert (await client.post(f"{url}/valve-lock", json=lock, headers=owner)).status_code == 409


async def test_only_the_buildings_owner_can_lock(client, make, world):
    other_owner = await make.user("owner")
    r = await client.post(
        f"/api/v1/homes/{world.home.id}/valve-lock",
        json={"state": "closed", "reason": "Someone else's building"},
        headers=await make.headers(other_owner),
    )
    assert r.status_code == 404


async def test_archived_home_cannot_be_controlled(client, make, world):
    owner = await make.headers(world.owner)
    await client.delete(f"/api/v1/homes/{world.home.id}", headers=owner)
    r = await client.post(_valve(world), json={"action": "open"}, headers=owner)
    assert r.status_code == 409
