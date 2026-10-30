"""FR-21: residents, occupancies and the billing contact (TC-21).
Bills (criteria 2 and 3) arrive in M6; their tests are added there."""

from __future__ import annotations

from datetime import date, timedelta

from ..conftest import PASSWORD, token_from_email


async def _invite(client, headers, home_id, email, billing=False):
    return await client.post(
        f"/api/v1/homes/{home_id}/invitations",
        headers=headers,
        json={
            "email": email,
            "full_name": "Flat mate",
            "move_in": str(date.today()),
            "is_billing_contact": billing,
        },
    )


async def test_several_residents_share_a_home_but_only_one_pays(client, make, outbox):
    world = await make.world()  # world.resident is already the billing contact
    headers = await make.headers(world.owner)
    r = await _invite(client, headers, world.home.id, "second@example.com", billing=True)
    assert r.status_code == 409
    r = await _invite(client, headers, world.home.id, f"mate-{world.home.id.hex[:6]}@example.com")
    assert r.status_code == 201
    token = token_from_email(outbox[-1][2])
    accepted = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert accepted.status_code == 200
    occupancies = (
        await client.get(f"/api/v1/homes/{world.home.id}/occupancies", headers=headers)
    ).json()
    assert len(occupancies) == 2
    assert sum(o["is_billing_contact"] for o in occupancies) == 1


async def test_the_database_itself_refuses_a_second_billing_contact(client, make):
    world = await make.world()
    mate = await make.user("resident")
    occupancy = await make.occupancy(world.home, mate)
    r = await client.patch(
        f"/api/v1/occupancies/{occupancy.id}",
        headers=await make.headers(world.owner),
        json={"is_billing_contact": True},
    )
    assert r.status_code == 409


async def test_two_invitations_racing_for_billing_contact_only_one_wins(client, make, outbox):
    world = await make.world()
    headers = await make.headers(world.owner)
    home = await make.home(world.building)
    tokens = []
    for n in range(2):
        r = await _invite(
            client, headers, home.id, f"race{n}-{home.id.hex[:6]}@example.com", billing=True
        )
        assert r.status_code == 201
        tokens.append(token_from_email(outbox[-1][2]))
    results = [
        (
            await client.post(
                "/api/v1/auth/invitations/accept", json={"token": t, "password": PASSWORD}
            )
        ).status_code
        for t in tokens
    ]
    assert sorted(results) == [200, 409]


async def test_moving_out_and_handing_over_billing(client, make):
    world = await make.world()
    headers = await make.headers(world.owner)
    occupancies = (
        await client.get(f"/api/v1/homes/{world.home.id}/occupancies", headers=headers)
    ).json()
    old = occupancies[0]
    too_early = date.fromisoformat(old["move_in"]) - timedelta(days=1)
    r = await client.patch(
        f"/api/v1/occupancies/{old['id']}", headers=headers, json={"move_out": str(too_early)}
    )
    assert r.status_code == 422
    r = await client.patch(
        f"/api/v1/occupancies/{old['id']}", headers=headers, json={"move_out": str(date.today())}
    )
    assert r.status_code == 200
    # Once the old billing contact has moved out, a new one can be chosen.
    newcomer = await make.user("resident")
    new = await make.occupancy(world.home, newcomer, move_in=date.today())
    r = await client.patch(
        f"/api/v1/occupancies/{new.id}", headers=headers, json={"is_billing_contact": True}
    )
    assert r.status_code == 200


async def test_resident_who_moved_out_loses_access_to_the_home(client, make):
    world = await make.world()
    resident_headers = await make.headers(world.resident)
    url = f"/api/v1/homes/{world.home.id}"
    assert (await client.get(url, headers=resident_headers)).status_code == 200
    occupancy = (
        await client.get(f"{url}/occupancies", headers=await make.headers(world.owner))
    ).json()[0]
    yesterday = date.today() - timedelta(days=1)
    await client.patch(
        f"/api/v1/occupancies/{occupancy['id']}",
        headers=await make.headers(world.owner),
        json={"move_out": str(yesterday)},
    )
    assert (await client.get(url, headers=resident_headers)).status_code == 404


async def test_cannot_invite_to_an_archived_home(client, make, outbox):
    world = await make.world()
    headers = await make.headers(world.owner)
    await client.delete(f"/api/v1/homes/{world.home.id}", headers=headers)
    r = await _invite(client, headers, world.home.id, "late@example.com")
    assert r.status_code == 409


async def test_invitation_reports_when_the_email_could_not_be_sent(client, make, monkeypatch):
    async def broken(to, subject, body):
        return False

    from app.routers import residents

    monkeypatch.setattr(residents, "send_email", broken)
    world = await make.world()
    r = await _invite(client, await make.headers(world.owner), world.home.id, "nomail@example.com")
    assert r.status_code == 201
    assert r.json()["email_sent"] is False
