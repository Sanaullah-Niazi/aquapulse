"""FR-28 (and FR-73's hash chain): the append-only audit log (TC-28)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import audit, db
from app.config import get_settings
from app.models import AuditEntry

from ..conftest import PASSWORD, TEST_DB, audit_actions


async def test_each_action_writes_exactly_one_entry(client, make, outbox):
    world = await make.world()
    headers = await make.headers(world.owner)
    building = f"/api/v1/buildings/{world.building.id}"
    steps = [
        ("building.updated", client.patch(building, headers=headers, json={"name": "B"})),
        ("home.created", client.post(f"{building}/homes", headers=headers, json={"unit_no": "Z9"})),
        (
            "home.updated",
            client.patch(f"/api/v1/homes/{world.home.id}", headers=headers, json={"floor": 2}),
        ),
        (
            "resident.invited",
            client.post(
                f"/api/v1/homes/{world.home.id}/invitations",
                headers=headers,
                json={"email": "a@example.com", "full_name": "A", "move_in": "2026-01-01"},
            ),
        ),
        ("home.archived", client.delete(f"/api/v1/homes/{world.home.id}", headers=headers)),
    ]
    for action, call in steps:
        before = await audit_actions(actor_id=world.owner.id)
        r = await call
        assert r.status_code < 300, (action, r.text)
        after = await audit_actions(actor_id=world.owner.id)
        assert after[len(before) :] == [action]


async def test_failed_login_is_audited_without_the_password(client, make):
    user = await make.user("owner")
    wrong = "wrong-guess-123"
    await client.post("/api/v1/auth/login", json={"email": user.email, "password": wrong})
    async with db.sessions()() as session:
        entry = await session.scalar(
            select(AuditEntry).where(
                AuditEntry.actor_id == user.id, AuditEntry.action == "auth.login_failed"
            )
        )
    assert entry is not None
    assert str(entry.ip) == "127.0.0.1"
    assert wrong not in str(entry.details)


async def test_api_database_role_cannot_change_or_delete_history(make):
    await make.user("owner")  # make sure there is at least one entry
    async with db.engine().connect() as conn:  # connects as the API's own role
        for sql in (
            "UPDATE audit_log SET action = 'x'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(sql))
            await conn.rollback()


async def test_even_the_owner_role_is_stopped_by_the_trigger():
    owner_engine = create_async_engine(get_settings().db_url(owner=True, database=TEST_DB))
    async with owner_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            await conn.execute(text("UPDATE audit_log SET action = 'x'"))
    await owner_engine.dispose()


async def test_hash_chain_detects_a_changed_entry(client, make):
    admin = await make.user("sysadmin")
    headers = await make.headers(admin)
    r = await client.post("/api/v1/admin/audit/verify", headers=headers)
    assert (r.json()["ok"], r.json()["first_broken_id"]) == (True, None)

    # A database superuser switches the guard off and edits history...
    owner_engine = create_async_engine(get_settings().db_url(owner=True, database=TEST_DB))
    async with owner_engine.connect() as conn:
        victim = await conn.scalar(text("SELECT min(id) FROM audit_log"))
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change"))
        await conn.execute(
            text("UPDATE audit_log SET ip = '6.6.6.6' WHERE id = :id"), {"id": victim}
        )
        ok, broken = await audit.verify_chain(AsyncSession(bind=conn))
        await conn.rollback()  # ...and we put everything back
    await owner_engine.dispose()
    assert (ok, broken) == (False, victim)


async def test_owner_filters_their_buildings_audit(client, make):
    world = await make.world()
    other = await make.world()
    headers = await make.headers(world.owner)
    await client.patch(
        f"/api/v1/buildings/{world.building.id}", headers=headers, json={"name": "C"}
    )
    await client.patch(
        f"/api/v1/buildings/{other.building.id}",
        headers=await make.headers(other.owner),
        json={"name": "D"},
    )
    url = f"/api/v1/buildings/{world.building.id}/audit"
    entries = (await client.get(url, headers=headers)).json()
    assert entries and all(e["building_id"] == str(world.building.id) for e in entries)

    by_actor = (
        await client.get(url, headers=headers, params={"actor_id": str(world.owner.id)})
    ).json()
    assert {e["actor_id"] for e in by_actor} == {str(world.owner.id)}
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert (await client.get(url, headers=headers, params={"since": future})).json() == []
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert (await client.get(url, headers=headers, params={"until": past})).json() == []


async def test_sysadmin_sees_all_entries_with_filters_and_paging(client, make):
    admin = await make.user("sysadmin")
    headers = await make.headers(admin)
    owner = await make.user("owner")
    await make.tokens(owner)
    r = await client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params={"actor_id": str(owner.id), "action": "auth.login"},
    )
    assert [e["action"] for e in r.json()] == ["auth.login"]
    page = await client.get(
        "/api/v1/admin/audit", headers=headers, params={"limit": 1, "offset": 1}
    )
    assert len(page.json()) == 1
    assert (
        await client.get("/api/v1/admin/audit", headers=headers, params={"limit": 501})
    ).status_code == 422


async def test_admin_owner_management_is_audited_and_disabling_logs_out(client, make):
    admin = await make.user("sysadmin")
    headers = await make.headers(admin)
    email = f"boss-{uuid.uuid4().hex[:6]}@example.com"
    r = await client.post(
        "/api/v1/admin/owners",
        headers=headers,
        json={"email": email, "full_name": "Boss", "password": PASSWORD},
    )
    assert r.status_code == 201
    owner_id = r.json()["id"]
    dup = await client.post(
        "/api/v1/admin/owners",
        headers=headers,
        json={"email": email, "full_name": "Boss", "password": PASSWORD},
    )
    assert dup.status_code == 409
    weak = await client.post(
        "/api/v1/admin/owners",
        headers=headers,
        json={"email": "w@example.com", "full_name": "W", "password": "password"},
    )
    assert weak.status_code == 422
    owners = (await client.get("/api/v1/admin/owners", headers=headers)).json()
    assert email in [o["email"] for o in owners]

    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    owner_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.patch(
        f"/api/v1/admin/owners/{owner_id}", headers=headers, json={"is_active": False}
    )
    assert r.json()["is_active"] is False
    assert (await client.get("/api/v1/me", headers=owner_headers)).status_code == 401
    missing = await client.patch(f"/api/v1/admin/owners/{uuid.uuid4()}", headers=headers, json={})
    assert missing.status_code == 404
    assert await audit_actions(actor_id=admin.id) == [
        "auth.login",
        "admin.owner_created",
        "admin.owner_updated",
    ]
