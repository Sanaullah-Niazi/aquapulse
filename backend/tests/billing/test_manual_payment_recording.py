"""FR-40: payments by cash, bank transfer or other, recorded by the owner."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import ProgrammingError

from app import db
from app.models import Payment

from ..conftest import audit_actions
from .conftest import issued_bill

pytestmark = pytest.mark.usefixtures("outbox")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


async def _pay(client, make, world, bill, amount, method="cash", **extra):
    return await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": amount, "method": method, **extra},
        headers=await make.headers(world.owner),
    )


async def _bill(client, make, user, bill) -> dict:
    r = await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(user))
    assert r.status_code == 200, r.text
    return r.json()


async def test_part_then_the_rest(client, make, world):
    bill = await issued_bill(client, make, world)  # Rs 540.00
    r = await _pay(client, make, world, bill, "200.00", reference="Receipt book #12")
    assert r.status_code == 201, r.text
    assert (r.json()["amount"], r.json()["method"]) == ("200.00", "cash")
    after = await _bill(client, make, world.owner, bill)
    assert (after["status"], after["amount_paid"], after["outstanding"]) == (
        "partially_paid", "200.00", "340.00",
    )  # fmt: skip
    # The resident sees it at once (criterion: "the resident sees the payment immediately").
    mine = await _bill(client, make, world.resident, bill)
    assert mine["status"] == "partially_paid" and len(mine["payments"]) == 1

    await _pay(client, make, world, bill, "340", method="bank", received_on="2026-10-20")
    done = await _bill(client, make, world.owner, bill)
    assert (done["status"], done["outstanding"]) == ("paid", "0.00")
    assert done["payments"][1]["received_on"] == "2026-10-20"


async def test_overpayment_and_too_many_decimals_are_refused(client, make, world):
    bill = await issued_bill(client, make, world)
    r = await _pay(client, make, world, bill, "540.01")
    assert r.status_code == 409 and "more than the outstanding" in r.json()["detail"]
    r = await _pay(client, make, world, bill, "10.005")  # PKR has 2 decimals
    assert r.status_code == 422
    for bad in ("0", "-5"):
        assert (await _pay(client, make, world, bill, bad)).status_code == 422
    # Stripe payments are recorded only by Stripe's webhook, never by hand.
    assert (await _pay(client, make, world, bill, "10", method="stripe_test")).status_code == 422


async def test_reversal_needs_a_reason_and_keeps_the_record(client, make, world):
    bill = await issued_bill(client, make, world)
    payment = (await _pay(client, make, world, bill, "540")).json()
    url = f"/api/v1/payments/{payment['id']}/reverse"
    headers = await make.headers(world.owner)
    assert (await client.post(url, json={"reason": "oops"}, headers=headers)).status_code == 422
    r = await client.post(url, json={"reason": "Recorded on the wrong bill"}, headers=headers)
    assert r.status_code == 200 and r.json()["reversed_at"] is not None
    after = await _bill(client, make, world.owner, bill)
    assert (after["status"], after["amount_paid"]) == ("issued", "0.00")
    assert after["payments"][0]["reversal_reason"] == "Recorded on the wrong bill"
    assert (
        await client.post(url, json={"reason": "Twice is too much"}, headers=headers)
    ).status_code == 409
    actions = await audit_actions(target_id=payment["id"])
    assert actions == ["payment.recorded", "payment.reversed"]


async def test_payments_cannot_be_deleted_from_the_database(world):
    """Criterion 3: the API's database account has no DELETE right on payments."""
    async with db.sessions()() as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(Payment.__table__.delete().where(Payment.id.is_(None)))


async def test_only_the_owner_records_payments(client, make, world):
    bill = await issued_bill(client, make, world)
    for user in (world.resident, await make.user("owner")):
        r = await client.post(
            f"/api/v1/bills/{bill['id']}/payments",
            json={"amount": "1", "method": "cash"},
            headers=await make.headers(user),
        )
        assert r.status_code in (403, 404)


async def test_receipt_photo(client, make, world):
    bill = await issued_bill(client, make, world)
    payment = (await _pay(client, make, world, bill, "100")).json()
    url = f"/api/v1/payments/{payment['id']}/receipt"
    owner = await make.headers(world.owner)
    r = await client.put(url, files={"file": ("receipt.png", PNG, "image/png")}, headers=owner)
    assert r.status_code == 200 and r.json()["has_receipt"] is True
    # The resident whose bill it is can see it; with the real type and nosniff.
    r = await client.get(url, headers=await make.headers(world.resident))
    assert r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    # The file type is checked from its content, not its name.
    fake = ("receipt.png", b"<script>alert(1)</script>", "image/png")
    assert (await client.put(url, files={"file": fake}, headers=owner)).status_code == 415
    big = ("big.png", PNG + b"\x00" * (2 * 1024 * 1024), "image/png")
    assert (await client.put(url, files={"file": big}, headers=owner)).status_code == 413


async def test_no_receipt_is_404(client, make, world):
    bill = await issued_bill(client, make, world)
    payment = (await _pay(client, make, world, bill, "100")).json()
    r = await client.get(
        f"/api/v1/payments/{payment['id']}/receipt", headers=await make.headers(world.owner)
    )
    assert r.status_code == 404
