"""FR-19: object-level access. The role may call the endpoint, but only for
their own buildings and homes; anything else looks like it does not exist (TC-19).
The every-endpoint x every-role check is in tests/security/test_rbac_matrix.py."""

from __future__ import annotations

from datetime import date, timedelta


async def test_resident_changing_the_home_id_gets_404(client, make):
    mine, other = await make.world(), await make.world()
    headers = await make.headers(mine.resident)
    assert (await client.get(f"/api/v1/homes/{mine.home.id}", headers=headers)).status_code == 200
    r = await client.get(f"/api/v1/homes/{other.home.id}", headers=headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}  # same answer as for an ID that does not exist


async def test_owner_cannot_read_or_change_another_owners_homes(client, make):
    mine, other = await make.world(), await make.world()
    headers = await make.headers(mine.owner)
    home_url = f"/api/v1/homes/{other.home.id}"
    building_url = f"/api/v1/buildings/{other.building.id}"
    assert (await client.get(home_url, headers=headers)).status_code == 404
    assert (await client.patch(home_url, headers=headers, json={"notes": "x"})).status_code == 404
    assert (await client.delete(home_url, headers=headers)).status_code == 404
    assert (await client.get(building_url, headers=headers)).status_code == 404
    assert (
        await client.patch(building_url, headers=headers, json={"name": "x"})
    ).status_code == 404
    assert (await client.get(f"{building_url}/homes", headers=headers)).status_code == 404
    r = await client.post(f"{building_url}/homes", headers=headers, json={"unit_no": "X1"})
    assert r.status_code == 404
    assert (await client.get(f"{building_url}/audit", headers=headers)).status_code == 404


async def test_owner_lists_only_their_own_buildings(client, make):
    mine, other = await make.world(), await make.world()
    r = await client.get("/api/v1/buildings", headers=await make.headers(mine.owner))
    ids = {b["id"] for b in r.json()}
    assert str(mine.building.id) in ids
    assert str(other.building.id) not in ids


async def test_sysadmin_sees_every_building(client, make):
    one, two = await make.world(), await make.world()
    admin = await make.user("sysadmin")
    r = await client.get("/api/v1/buildings", headers=await make.headers(admin))
    ids = {b["id"] for b in r.json()}
    assert {str(one.building.id), str(two.building.id)} <= ids


async def test_resident_sees_home_only_during_their_occupancy(client, make):
    world = await make.world()
    past = await make.user("resident")
    future = await make.user("resident")
    today = date.today()
    await make.occupancy(world.home, past, today - timedelta(days=60), today - timedelta(days=1))
    await make.occupancy(world.home, future, move_in=today + timedelta(days=10))
    url = f"/api/v1/homes/{world.home.id}"
    assert (await client.get(url, headers=await make.headers(past))).status_code == 404
    assert (await client.get(url, headers=await make.headers(future))).status_code == 404


async def test_resident_cannot_see_an_archived_home(client, make):
    world = await make.world()
    owner_h = await make.headers(world.owner)
    await client.delete(f"/api/v1/homes/{world.home.id}", headers=owner_h)
    r = await client.get(
        f"/api/v1/homes/{world.home.id}", headers=await make.headers(world.resident)
    )
    assert r.status_code == 404


async def test_owner_cannot_manage_occupancies_in_another_building(client, make):
    mine, other = await make.world(), await make.world()
    headers = await make.headers(mine.owner)
    r = await client.get(f"/api/v1/homes/{other.home.id}/occupancies", headers=headers)
    assert r.status_code == 404
    other_occupancies = await client.get(
        f"/api/v1/homes/{other.home.id}/occupancies", headers=await make.headers(other.owner)
    )
    occupancy_id = other_occupancies.json()[0]["id"]
    r = await client.patch(
        f"/api/v1/occupancies/{occupancy_id}", headers=headers, json={"move_out": str(date.today())}
    )
    assert r.status_code == 404


async def test_me_lists_only_my_own_things(client, make):
    world = await make.world()
    owner_me = (await client.get("/api/v1/me", headers=await make.headers(world.owner))).json()
    assert owner_me["building_ids"] == [str(world.building.id)]
    resident_me = (
        await client.get("/api/v1/me", headers=await make.headers(world.resident))
    ).json()
    assert resident_me["home_ids"] == [str(world.home.id)]
    assert resident_me["building_ids"] == []
