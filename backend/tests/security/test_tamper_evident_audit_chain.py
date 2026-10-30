"""FR-73: every audit entry's hash covers the previous hash. The daily check (and
the System Admin's on-demand check) finds a changed row, opens a critical
alert for System Admins, and checks 100,000 entries in under 30 seconds."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app import audit, db, worker
from app.config import get_settings
from app.models import Alert, AuditCheck, AuditEntry, Notification

from ..conftest import TEST_DB

pytestmark = pytest.mark.usefixtures("outbox")


@asynccontextmanager
async def superuser():
    """The database owner: the only one who can switch the append-only guard off."""
    engine = create_async_engine(get_settings().db_url(owner=True, database=TEST_DB))
    async with engine.begin() as conn:
        yield conn
    await engine.dispose()


async def _without_guard(conn: AsyncConnection, sql: str, **params) -> None:
    await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change"))
    await conn.execute(text(sql), params)
    await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change"))


async def _entries(count: int) -> list[int]:
    """Write `count` real audit entries (a fresh test database may have none)."""
    async with db.sessions()() as session, session.begin():
        made = [await audit.record(session, "test.entry", actor_role="system", ip="10.0.0.1",
                                   details={"n": n}) for n in range(count)]  # fmt: skip
        return [entry.id for entry in made]


async def _due_day() -> datetime:
    """A time when the daily check is due, even if other tests ran the worker
    at future dates (the billing tests do)."""
    async with db.sessions()() as session:
        last = await session.scalar(
            select(func.max(AuditCheck.at)).where(AuditCheck.requested_by.is_(None))
        )
    return max(datetime.now(UTC), last or datetime.now(UTC)) + timedelta(days=2)


async def _latest_check() -> AuditCheck:
    async with db.sessions()() as session:
        return await session.scalar(select(AuditCheck).order_by(AuditCheck.at.desc()).limit(1))


async def _audit_alert() -> Alert | None:
    async with db.sessions()() as session:
        return await session.scalar(
            select(Alert).where(
                Alert.dedupe_key == "audit_chain", Alert.state.in_(("open", "acknowledged"))
            )
        )


async def test_a_changed_row_is_found_and_admins_are_alerted(client, make):
    """Criterion 1."""
    admin = await make.user("sysadmin")
    victim, ip = (await _entries(3))[1], "10.0.0.1"
    async with superuser() as conn:
        await _without_guard(conn, "UPDATE audit_log SET ip = '6.6.6.6' WHERE id = :id", id=victim)
    try:
        # The worker's daily round (a day later than any check so far).
        assert await worker.daily_audit_check(await _due_day()) == 1
        check = await _latest_check()  # the newest: the one just made
        assert (check.ok, check.first_broken_id, check.requested_by) == (False, victim, None)
        alert = await _audit_alert()
        assert alert is not None and alert.severity == "critical" and alert.building_id is None
        assert f"Audit entry {victim} was changed" in alert.message
        async with db.sessions()() as session:
            told = set(await session.scalars(
                select(Notification.user_id).where(Notification.alert_id == alert.id)))  # fmt: skip
        assert admin.id in told

        # Only System Admins see a system alert.
        r = await client.get(f"/api/v1/alerts/{alert.id}", headers=await make.headers(admin))
        assert r.status_code == 200
        owner = await make.headers(await make.user("owner"))
        assert (await client.get(f"/api/v1/alerts/{alert.id}", headers=owner)).status_code == 404
    finally:
        async with superuser() as conn:  # put history back as it was
            await _without_guard(conn, "UPDATE audit_log SET ip = :ip WHERE id = :id",
                                 ip=ip, id=victim)  # fmt: skip

    # Checked again on demand: fine, and the alert's problem is gone.
    r = await client.post("/api/v1/admin/audit/verify", headers=await make.headers(admin))
    assert r.json()["ok"] is True and r.json()["requested_by"] == str(admin.id)
    async with db.sessions()() as session:
        alert = await session.get(Alert, alert.id)
        assert alert.cleared_at is not None
    # A System Admin can close it at once (it would auto-resolve in 10 minutes).
    r = await client.post(f"/api/v1/alerts/{alert.id}/resolve", json={"note": "Restored"},
                          headers=await make.headers(admin))  # fmt: skip
    assert r.status_code == 200

    r = await client.get("/api/v1/admin/audit/checks", headers=await make.headers(admin))
    checks = {(c["ok"], c["requested_by"]) for c in r.json()}
    assert {(False, None), (True, str(admin.id))} <= checks  # the daily one and the admin's


async def test_a_removed_row_is_found():
    middle = (await _entries(3))[1]
    async with superuser() as conn:
        row = (await conn.execute(text("SELECT * FROM audit_log WHERE id = :id"),
                                  {"id": middle})).mappings().one()  # fmt: skip
        await _without_guard(conn, "DELETE FROM audit_log WHERE id = :id", id=middle)
    try:
        async with db.sessions()() as session:
            ok, broken = await audit.verify_chain(session)
        assert (ok, broken) == (False, middle + 1)  # the next entry no longer fits
    finally:
        async with superuser() as conn:
            await conn.execute(insert(AuditEntry.__table__).values(**row))


def _chain(prev: bytes, count: int) -> list[dict]:
    """`count` valid entries after `prev`, made the same way audit.record makes them."""
    rows, at = [], datetime(2026, 1, 1, tzinfo=UTC)
    for n in range(count):
        entry = SimpleNamespace(
            at=at + timedelta(seconds=n), actor_id=None, actor_role="system", ip=None,
            action="test.bulk", target_type="test", target_id=str(n), building_id=None,
            details={"n": n}, prev_hash=prev,
        )  # fmt: skip
        entry.hash = audit._digest(entry)
        rows.append(vars(entry))
        prev = entry.hash
    return rows


def test_the_digest_is_sha256_of_previous_hash_and_content():
    [row] = _chain(b"\x01" * 32, 1)
    content = json.dumps(
        {"at": row["at"].isoformat(), "actor_id": None, "actor_role": "system", "ip": None,
         "action": "test.bulk", "target_type": "test", "target_id": "0", "building_id": None,
         "details": {"n": 0}}, sort_keys=True, separators=(",", ":"),
    )  # fmt: skip
    assert row["hash"] == hashlib.sha256(b"\x01" * 32 + content.encode()).digest()


async def test_100000_entries_are_checked_in_under_30_seconds():
    """Criterion 2."""
    await _entries(1)
    async with superuser() as conn:
        last_id, prev = (await conn.execute(text(
            "SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1"))).one()  # fmt: skip
        rows = _chain(prev, 100_000)
        for start in range(0, len(rows), 5000):
            await conn.execute(insert(AuditEntry.__table__), rows[start : start + 5000])
    try:
        async with db.sessions()() as session, session.begin():
            started = time.perf_counter()
            check = await audit.check_chain(session)
            took = time.perf_counter() - started
        assert check.ok and check.entries >= 100_000
        assert took < 30, f"{took:.1f} s"
        print(f"\n  audit chain: {check.entries} entries checked in {took:.1f} s")
    finally:
        async with superuser() as conn:  # remove the test entries again (from the end)
            await _without_guard(conn, "DELETE FROM audit_log WHERE id > :id", id=last_id)


async def test_the_daily_check_runs_once_a_day():
    now = await _due_day()
    assert await worker.daily_audit_check(now) == 1
    assert await worker.daily_audit_check(now + timedelta(hours=23)) == 0
    assert await worker.daily_audit_check(now + timedelta(hours=24)) == 1
