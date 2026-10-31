"""FR-75 (TC-75): download my data, delete my account, 13-month retention."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app import db
from app.config import get_settings
from app.models import Bill, Home, Payment, User
from app.privacy import DELETED_NAME, deleted_email

from ..billing.conftest import issued_bill
from ..billing.conftest import world as billing_world  # noqa: F401 (fixture)
from ..conftest import PASSWORD, TEST_DB, audit_actions

pytestmark = pytest.mark.usefixtures("outbox")


async def _resident_with_a_bill(client, make, world):
    """The billing world's resident, with a unique name and phone, an issued
    September bill and a payment whose note an owner typed with the name in it."""
    name = f"Ayesha Test {uuid.uuid4().hex[:8]}"
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id == world.resident.id)
            .values(full_name=name, phone="+92 300 7654321")
        )
    bill = await issued_bill(client, make, world)
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "100", "method": "cash", "reference": f"cash from {name}"},
        headers=await make.headers(world.owner),
    )
    assert r.status_code in (200, 201), r.text
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Home).where(Home.id == world.home.id).values(notes=f"{name} has a cat")
        )
    return name, bill


async def test_export_has_profile_homes_bills_payments_and_use(client, make, billing_world):  # noqa: F811
    world = billing_world
    name, bill = await _resident_with_a_bill(client, make, world)
    r = await client.get("/api/v1/me/export", headers=await make.headers(world.resident))
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["profile"]["email"] == world.resident.email
    assert data["profile"]["full_name"] == name
    assert data["profile"]["phone"] == "+92 300 7654321"
    assert "password_hash" not in data["profile"] and "totp_secret" not in data["profile"]
    [occ] = data["occupancies"]
    assert occ["unit_no"] == "A-1" and occ["billing_contact"] is True
    assert sum(float(d["litres"]) for d in occ["daily_litres"]) == pytest.approx(7000, abs=1)
    [b] = data["bills"]
    assert b["bill_no"] == bill["bill_no"] and b["lines"]
    assert b["payments"][0]["amount"].startswith("100")
    assert "user.data_exported" in await audit_actions(actor_id=world.resident.id)


async def test_deletion_removes_name_and_email_everywhere_but_keeps_bill_totals(
    client,
    make,
    billing_world,  # noqa: F811
):
    world = billing_world
    name, bill = await _resident_with_a_bill(client, make, world)
    email = world.resident.email
    headers = await make.headers(world.resident)

    wrong = await client.post(
        "/api/v1/me/delete", json={"password": "nope", "confirm": "DELETE"}, headers=headers
    )
    assert wrong.status_code == 400
    no_confirm = await client.post(
        "/api/v1/me/delete", json={"password": PASSWORD, "confirm": "yes"}, headers=headers
    )
    assert no_confirm.status_code == 422

    r = await client.post(
        "/api/v1/me/delete", json={"password": PASSWORD, "confirm": "DELETE"}, headers=headers
    )
    assert r.status_code == 200, r.text

    # Logged out everywhere, and the old login no longer works.
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 401

    # AC2: the name and email are in no table at all (every row of every
    # table, as text), read as the database owner so no table is hidden.
    engine = create_async_engine(get_settings().db_url(owner=True, database=TEST_DB))
    async with engine.connect() as conn:
        tables = (
            await conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        ).all()
        assert "users" in tables and "bills" in tables
        for table in tables:
            for needle in (email, name):
                hits = await conn.scalar(
                    text(f'SELECT count(*) FROM "{table}" t WHERE strpos(t::text, :n) > 0'),  # noqa: S608
                    {"n": needle},
                )
                assert hits == 0, f"{needle!r} is still in table {table}"
    await engine.dispose()

    async with db.sessions()() as session:
        user = await session.get(User, world.resident.id)
        assert user.email == deleted_email(user.id) and user.full_name == DELETED_NAME
        assert user.phone is None and not user.is_active and user.password_hash is None
        kept = await session.get(Bill, uuid.UUID(bill["id"]))
        assert kept.total == pytest.approx(float(bill["total"]))  # bill totals remain
        assert kept.bill_to == DELETED_NAME
        payment = await session.scalar(select(Payment).where(Payment.bill_id == kept.id))
        assert payment.reference == f"cash from {DELETED_NAME}"
    assert "user.deleted" in await audit_actions(actor_id=world.resident.id)


async def test_only_residents_can_delete_themselves(client, make):
    owner = await make.user("owner")
    r = await client.post(
        "/api/v1/me/delete",
        json={"password": PASSWORD, "confirm": "DELETE"},
        headers=await make.headers(owner),
    )
    assert r.status_code == 403


async def test_failed_logins_keep_no_email_in_the_audit_log(client, make):
    user = await make.user("owner")
    await client.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong-1"})
    async with db.sessions()() as session:
        hits = await session.scalar(
            text("SELECT count(*) FROM audit_log WHERE strpos(details::text, :e) > 0"),
            {"e": user.email},
        )
    assert hits == 0


async def test_raw_readings_are_kept_13_months(client):
    """AC3: TimescaleDB drops raw reading chunks after 395 days (13 months)."""
    async with db.sessions()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT hypertable_name, config->>'drop_after'"
                    " FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'"
                )
            )
        ).all()
    policies = dict(rows)
    assert policies.get("telemetry") == "395 days"
    assert policies.get("tank_readings") == "395 days"
