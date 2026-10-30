"""FR-33: tariffs set by the owner, with versions. Also the preview (FR-45)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ..conftest import audit_actions
from .conftest import SEPT, bills_of, generate, issued_bill, use_water

pytestmark = pytest.mark.usefixtures("outbox")

SLABS = [
    {"from_l": 0, "to_l": 5000, "rate_per_kl": "70"},
    {"from_l": 5000, "to_l": None, "rate_per_kl": "150"},
]


async def _create(client, make, world, **body):
    body = {"name": "New rates", "effective_from": "2027-01-01", "slabs": SLABS} | body
    return await client.post(
        f"/api/v1/buildings/{world.building.id}/tariffs",
        json=body,
        headers=await make.headers(world.owner),
    )


async def test_create_list_and_read_versions(client, make, world):
    r = await _create(
        client, make, world, charges=[{"name": "Sewerage", "kind": "extra_percent", "value": "10"}]
    )
    assert r.status_code == 201, r.text
    tariff = r.json()
    assert (tariff["version"], tariff["currency_code"]) == (2, "PKR")  # v1 is the fixture's
    assert tariff["slabs"][1] == {"from_l": 5000, "to_l": None, "rate_per_kl": "150"}
    assert tariff["charges"] == [{"name": "Sewerage", "kind": "extra_percent", "value": "10"}]
    assert "tariff.created" in await audit_actions(target_id=tariff["id"])

    headers = await make.headers(world.owner)
    listed = (
        await client.get(f"/api/v1/buildings/{world.building.id}/tariffs", headers=headers)
    ).json()
    assert [t["version"] for t in listed] == [2, 1]
    r = await client.get(f"/api/v1/tariffs/{tariff['id']}", headers=headers)
    assert r.json()["name"] == "New rates"
    other = await make.user("owner")
    r = await client.get(f"/api/v1/tariffs/{tariff['id']}", headers=await make.headers(other))
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("slabs", "charges", "message"),
    [
        ([{"from_l": 0, "to_l": 5000, "rate_per_kl": "1"}, {"from_l": 6000, "to_l": None,
          "rate_per_kl": "2"}], [], "no gaps"),
        ([{"from_l": 0, "to_l": 5000, "rate_per_kl": "1"}], [], "no upper limit"),
        ([{"from_l": 10, "to_l": None, "rate_per_kl": "1"}], [], "start at 0"),
        ([{"from_l": 0, "to_l": None, "rate_per_kl": "1"}],
         [{"name": "VAT", "kind": "tax_percent", "value": "150"}], "0 to 100"),
        ([{"from_l": 0, "to_l": None, "rate_per_kl": "1"}],
         [{"name": "Fee", "kind": "extra_fixed", "value": "1.005"}], "at most 2 decimals"),
    ],
)  # fmt: skip
async def test_invalid_tariffs_are_refused(client, make, world, slabs, charges, message):
    """Criteria 1 and 2, through the API (422 with the rule that was broken)."""
    r = await _create(client, make, world, slabs=slabs, charges=charges)
    assert r.status_code == 422 and message in r.json()["detail"]


async def test_negative_rates_and_unknown_fields_are_refused(client, make, world):
    bad = [{"from_l": 0, "to_l": None, "rate_per_kl": "-1"}]
    assert (await _create(client, make, world, slabs=bad)).status_code == 422
    assert (await _create(client, make, world, fixed_fee="100")).status_code == 422


async def test_a_new_version_does_not_change_issued_bills(client, make, world):
    """Criterion 3."""
    bill = await issued_bill(client, make, world)
    r = await _create(client, make, world, effective_from=str(date.today() + timedelta(days=30)))
    assert r.status_code == 201
    await generate(client, make, world)
    [again] = await bills_of(client, make, world)
    assert (again["id"], again["total"]) == (bill["id"], bill["total"])


async def test_a_new_version_cannot_reach_back_into_billed_months(client, make, world):
    await issued_bill(client, make, world)
    r = await _create(client, make, world, effective_from="2026-09-15")
    assert r.status_code == 409 and "already issued" in r.json()["detail"]


async def test_two_versions_in_one_month_split_the_litres_by_date(client, make, world):
    """Criterion 4: v2 starts on 16 September. 100 L/day before, 300 L/day after."""
    r = await _create(client, make, world, effective_from="2026-09-16")
    assert r.status_code == 201
    await use_water(world.home, world.device, SEPT, [100.0] * 15 + [300.0] * 15,
                    world.building.timezone)  # fmt: skip
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    detail = (
        await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    ).json()
    # v1 (15 days): 1,500 L, slab 1 = 0-2,500 L at 60 -> 90.00
    # v2 (15 days): 4,500 L, slab 1 = 0-2,500 L at 70 -> 175.00, then 2,000 L at 150 -> 300.00
    assert [(ln["description"], ln["amount"]) for ln in detail["lines"]] == [
        ("Tariff v1: Slab 1 (0-2,500 L, 15/30 days)", "90.00"),
        ("Tariff v2: Slab 1 (0-2,500 L, 15/30 days)", "175.00"),
        ("Tariff v2: Slab 2 (2,500+ L, 15/30 days)", "300.00"),
    ]
    assert detail["total"] == "565.00"


async def test_preview_uses_the_same_engine(client, make, world):
    body = {
        "currency_code": "KWD",
        "litres": "7000",
        "slabs": [
            {"from_l": 0, "to_l": 5000, "rate_per_kl": "0.250"},
            {"from_l": 5000, "to_l": None, "rate_per_kl": "0.500"},
        ],
        "charges": [{"name": "Sewerage", "kind": "extra_percent", "value": "10"}],
    }
    headers = await make.headers(world.owner)
    r = await client.post("/api/v1/tariffs/preview", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["total"], r.json()["total_text"]) == ("2.475", "KWD 2.475")
    assert [ln["amount"] for ln in r.json()["lines"]] == ["1.250", "1.000", "0.225"]

    bad = body | {"days": 31, "month_days": 30}
    assert (
        await client.post("/api/v1/tariffs/preview", json=bad, headers=headers)
    ).status_code == 422
    bad = body | {"currency_code": "XYZ"}
    r = await client.post("/api/v1/tariffs/preview", json=bad, headers=headers)
    assert r.status_code == 422 and "Unknown currency" in r.json()["detail"]
