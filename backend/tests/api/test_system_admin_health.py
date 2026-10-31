"""FR-55 criterion 1: the System Admin health page shows red or green for the
database, the broker, the ingestor and the worker, with devices online,
readings per minute and refused messages (FR-31)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update

from app import db, heartbeat
from app.models import ServiceHeartbeat

URL = "/api/v1/admin/health"


async def _health(client, headers) -> dict:
    r = await client.get(URL, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _service(body: dict, name: str) -> dict:
    return next(s for s in body["services"] if s["name"] == name)


async def test_red_and_green_per_service(client, make):
    async with db.sessions()() as session, session.begin():
        await session.execute(delete(ServiceHeartbeat))
    headers = await make.headers(await make.user("sysadmin"))

    body = await _health(client, headers)
    assert [s["name"] for s in body["services"]] == ["database", "broker", "ingestor", "worker"]
    assert _service(body, "database")["ok"] is True
    assert _service(body, "broker")["ok"] is True
    # The ingestor and the worker never said "alive": red, and so not ok overall.
    assert _service(body, "ingestor") | {"seen_at": None} == {
        "name": "ingestor", "ok": False, "detail": "Never reported. Is it running?",
        "seen_at": None,
    }  # fmt: skip
    assert body["ok"] is False

    await heartbeat.beat("ingestor", {"connected": True, "received": 12,
                                      "rejected": {"bad_json": 2}})  # fmt: skip
    await heartbeat.beat("worker")
    body = await _health(client, headers)
    assert _service(body, "ingestor")["ok"] and _service(body, "worker")["ok"]
    assert body["ok"] is True
    assert body["received_since_start"] == 12
    assert body["rejected_since_start"] == {"bad_json": 2}

    # A worker that stopped 5 minutes ago is red again.
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(ServiceHeartbeat)
            .where(ServiceHeartbeat.service == "worker")
            .values(seen_at=datetime.now(UTC) - timedelta(minutes=5))
        )
    worker = _service(await _health(client, headers), "worker")
    assert worker["ok"] is False and worker["detail"] == "No sign of life for 5 min"


async def test_devices_online_are_counted(client, make):
    headers = await make.headers(await make.user("sysadmin"))
    before = await _health(client, headers)
    world = await make.world()
    await make.device(world.home, online=True)
    after = await _health(client, headers)
    assert after["devices_active"] == before["devices_active"] + 1
    assert after["devices_online"] == before["devices_online"] + 1
    assert isinstance(after["readings_last_minute"], int)
    assert after["last_backup_at"] is None  # not set up yet: shown as such, never invented


async def test_only_system_admins(client, make):
    for role in ("owner", "resident"):
        r = await client.get(URL, headers=await make.headers(await make.user(role)))
        assert r.status_code == 403
