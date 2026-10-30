"""FR-20: buildings and homes (TC-20)."""

from __future__ import annotations

from ..conftest import audit_actions


async def test_owner_creates_and_edits_a_building(client, make):
    owner = await make.user("owner")
    headers = await make.headers(owner)
    r = await client.post(
        "/api/v1/buildings",
        headers=headers,
        json={
            "name": "Gulf View",
            "address": "Doha",
            "timezone": "Asia/Qatar",
            "currency_code": "QAR",
        },
    )
    assert r.status_code == 201, r.text
    building = r.json()
    assert building["owner_id"] == str(owner.id)
    assert building["currency_code"] == "QAR"

    r = await client.patch(
        f"/api/v1/buildings/{building['id']}",
        headers=headers,
        json={"name": "Gulf View 2", "due_days": 20},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Gulf View 2"
    assert r.json()["due_days"] == 20
    got = await client.get(f"/api/v1/buildings/{building['id']}", headers=headers)
    assert got.json()["name"] == "Gulf View 2"


async def test_all_eleven_currencies_are_accepted_and_others_are_not(client, make):
    owner = await make.user("owner")
    headers = await make.headers(owner)
    for code in ("PKR", "USD", "GBP", "EUR", "AUD", "AED", "SAR", "QAR", "KWD", "BHD", "OMR"):
        r = await client.post(
            "/api/v1/buildings", headers=headers, json={"name": code, "currency_code": code}
        )
        assert r.status_code == 201, code
    r = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "x", "currency_code": "JPY"}
    )
    assert r.status_code == 422


async def test_unknown_time_zone_is_rejected(client, make):
    headers = await make.headers(await make.user("owner"))
    r = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "x", "timezone": "Mars/Base"}
    )
    assert r.status_code == 422


async def test_owner_cannot_create_a_building_for_someone_else(client, make):
    owner, other = await make.user("owner"), await make.user("owner")
    r = await client.post(
        "/api/v1/buildings",
        headers=await make.headers(owner),
        json={"name": "x", "owner_id": str(other.id)},
    )
    assert r.status_code == 403


async def test_sysadmin_creates_a_building_for_an_owner_and_can_reassign_it(client, make):
    admin, owner, owner2 = (
        await make.user("sysadmin"),
        await make.user("owner"),
        await make.user("owner"),
    )
    headers = await make.headers(admin)
    missing = await client.post("/api/v1/buildings", headers=headers, json={"name": "x"})
    assert missing.status_code == 422
    resident = await make.user("resident")
    not_owner = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "x", "owner_id": str(resident.id)}
    )
    assert not_owner.status_code == 422
    r = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "Assigned", "owner_id": str(owner.id)}
    )
    assert r.status_code == 201
    building_id = r.json()["id"]
    r = await client.patch(
        f"/api/v1/buildings/{building_id}", headers=headers, json={"owner_id": str(owner2.id)}
    )
    assert r.status_code == 200
    assert r.json()["owner_id"] == str(owner2.id)


async def test_owner_cannot_give_their_building_away(client, make):
    world = await make.world()
    other = await make.user("owner")
    r = await client.patch(
        f"/api/v1/buildings/{world.building.id}",
        headers=await make.headers(world.owner),
        json={"owner_id": str(other.id)},
    )
    assert r.status_code == 403


async def test_homes_crud_with_unique_unit_numbers(client, make):
    world = await make.world()
    headers = await make.headers(world.owner)
    url = f"/api/v1/buildings/{world.building.id}/homes"
    r = await client.post(
        url, headers=headers, json={"unit_no": "A-101", "floor": 1, "occupants": 3}
    )
    assert r.status_code == 201
    home = r.json()
    duplicate = await client.post(url, headers=headers, json={"unit_no": "A-101"})
    assert duplicate.status_code == 409
    other = await client.post(url, headers=headers, json={"unit_no": "A-102"})
    rename_clash = await client.patch(
        f"/api/v1/homes/{other.json()['id']}", headers=headers, json={"unit_no": "A-101"}
    )
    assert rename_clash.status_code == 409
    r = await client.patch(
        f"/api/v1/homes/{home['id']}", headers=headers, json={"notes": "corner flat"}
    )
    assert r.json()["notes"] == "corner flat"
    units = [h["unit_no"] for h in (await client.get(url, headers=headers)).json()]
    assert {"A-101", "A-102"} <= set(units)


async def test_same_unit_number_is_fine_in_another_building(client, make):
    one, two = await make.world(), await make.world()
    for world in (one, two):
        r = await client.post(
            f"/api/v1/buildings/{world.building.id}/homes",
            headers=await make.headers(world.owner),
            json={"unit_no": "G-1"},
        )
        assert r.status_code == 201


async def test_deleting_a_home_archives_it_and_keeps_it(client, make):
    world = await make.world()
    headers = await make.headers(world.owner)
    r = await client.delete(f"/api/v1/homes/{world.home.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None
    url = f"/api/v1/buildings/{world.building.id}/homes"
    active = [h["id"] for h in (await client.get(url, headers=headers)).json()]
    everything = [
        h["id"]
        for h in (await client.get(url, headers=headers, params={"include_archived": True})).json()
    ]
    assert str(world.home.id) not in active
    assert str(world.home.id) in everything
    # Archiving twice changes nothing and writes one audit entry.
    await client.delete(f"/api/v1/homes/{world.home.id}", headers=headers)
    assert (await audit_actions(target_id=str(world.home.id))).count("home.archived") == 1


async def test_building_and_home_changes_are_audited_with_before_and_after(client, make):
    world = await make.world()
    headers = await make.headers(world.owner)
    await client.patch(
        f"/api/v1/buildings/{world.building.id}", headers=headers, json={"name": "New"}
    )
    r = await client.get(
        f"/api/v1/buildings/{world.building.id}/audit",
        headers=headers,
        params={"action": "building.updated"},
    )
    assert r.json()[0]["details"] == {"name": ["Test Tower", "New"]}
