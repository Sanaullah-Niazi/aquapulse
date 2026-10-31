"""GET /homes/{id}/insights (FR-63, FR-66) and the owner's auto shut-off switch (FR-61)."""

from __future__ import annotations

from sqlalchemy import select

from app import db
from app.models import AuditEntry

from .conftest import feed


async def test_insights_show_the_reason_and_evidence_to_who_may_see_the_home(
    site, engine, client, make
):
    site.twin.inject("burst", site.twin.device(1), 20, lpm=30.0)
    await feed(engine, site.twin, 7 * 60)

    r = await client.get(
        f"/api/v1/homes/{site.homes[0].id}/insights", headers=await make.headers(site.owner)
    )
    assert r.status_code == 200, r.text
    [found] = r.json()
    assert found["kind"] == "burst" and found["severity"] == "critical"
    assert found["high_confidence"] is True and found["confidence"] >= 0.9
    assert "30.0 L/min" in found["explanation"]
    assert found["evidence"]["points"] and found["evidence"]["burst_lpm"] >= 20
    assert "confidence" not in found["evidence"]  # moved to its own field

    # The resident of home 1 sees it; home 2 has none.
    resident = await make.headers(site.resident)
    r = await client.get(f"/api/v1/homes/{site.homes[0].id}/insights", headers=resident)
    assert r.status_code == 200 and len(r.json()) == 1
    r = await client.get(
        f"/api/v1/homes/{site.homes[1].id}/insights", headers=await make.headers(site.owner)
    )
    assert r.json() == []

    # Someone else's home: not found.
    stranger = await make.headers(await make.user("resident"))
    r = await client.get(f"/api/v1/homes/{site.homes[0].id}/insights", headers=stranger)
    assert r.status_code == 404


async def test_the_owner_switches_auto_shutoff_on_and_it_is_audited(site, client, make):
    headers = await make.headers(site.owner)
    url = f"/api/v1/homes/{site.homes[0].id}"
    assert (await client.get(url, headers=headers)).json()["auto_shutoff_enabled"] is False
    r = await client.patch(url, json={"auto_shutoff_enabled": True}, headers=headers)
    assert r.status_code == 200 and r.json()["auto_shutoff_enabled"] is True
    async with db.sessions()() as session:
        entry = await session.scalar(
            select(AuditEntry)
            .where(
                AuditEntry.action == "home.updated", AuditEntry.target_id == str(site.homes[0].id)
            )
            .order_by(AuditEntry.id.desc())
        )
    assert entry is not None and "auto_shutoff_enabled" in str(entry.details)
    # A resident cannot change it.
    r = await client.patch(
        url, json={"auto_shutoff_enabled": False}, headers=await make.headers(site.resident)
    )
    assert r.status_code == 403
