"""FR-39: online payment in Stripe TEST mode.

No network: creating the Checkout page is replaced by a stand-in that returns
a made-up session, and the webhooks are signed here with a test secret using
Stripe's documented scheme (HMAC-SHA256 of "timestamp.body"), then checked
by the real Stripe library, exactly as in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import date

import pytest
from pydantic import SecretStr

from app.billing import stripe_pay
from app.billing.stripe_pay import CheckoutPage, StripeProvider
from app.config import get_settings

from ..conftest import audit_actions
from .conftest import SEPT, add_tariff, bills_of, generate, issued_bill, use_water

pytestmark = pytest.mark.usefixtures("outbox")

WEBHOOK_SECRET = "whsec_test_" + "a" * 32
WEBHOOK = "/api/v1/webhooks/stripe"


@pytest.fixture
def stripe_on(monkeypatch):
    """Stripe 'configured' with test keys; Checkout pages are made up locally."""
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_" + "x" * 24))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(WEBHOOK_SECRET))
    stripe_pay.provider.cache_clear()
    created: list[dict] = []

    async def fake_create_checkout(self, **params):
        created.append(params)
        session_id = f"cs_test_{uuid.uuid4().hex}"
        return CheckoutPage(session_id, f"https://checkout.stripe.com/c/pay/{session_id}")

    monkeypatch.setattr(StripeProvider, "create_checkout", fake_create_checkout)
    yield created
    stripe_pay.provider.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_provider():
    stripe_pay.provider.cache_clear()
    yield
    stripe_pay.provider.cache_clear()


def signed(event: dict, secret: str = WEBHOOK_SECRET, at: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    ts = at or int(time.time())
    signature = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={signature}", "Content-Type": "application/json"}


def paid_event(session_id: str, amount_minor: int, currency: str, kind=None) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex}",
        "object": "event",
        "type": kind or "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "payment_status": "paid",
                "amount_total": amount_minor,
                "currency": currency.lower(),
            }
        },
    }


async def _checkout(client, make, world, bill) -> dict:
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/checkout", headers=await make.headers(world.resident)
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _bill(client, make, world, bill) -> dict:
    r = await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    return r.json()


async def test_without_stripe_only_manual_payments(client, make, world):
    """Criterion 4: no keys -> no 'pay online', the webhook does not exist."""
    bill = await issued_bill(client, make, world)
    assert (await _bill(client, make, world, bill))["can_pay_online"] is False
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/checkout", headers=await make.headers(world.resident)
    )
    assert r.status_code == 409 and "not set up" in r.json()["detail"]
    body, headers = signed(paid_event("cs_test_x", 1, "PKR"))
    assert (await client.post(WEBHOOK, content=body, headers=headers)).status_code == 404


def test_live_keys_are_refused(monkeypatch, caplog):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_live_" + "x" * 24))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(WEBHOOK_SECRET))
    with caplog.at_level(logging.ERROR):
        assert stripe_pay.provider() is None
    assert "not a TEST key" in caplog.text


def test_a_missing_webhook_secret_switches_it_off(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_" + "x" * 24))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(""))
    assert stripe_pay.provider() is None


async def test_checkout_asks_stripe_for_the_exact_amount(client, make, world, stripe_on):
    bill = await issued_bill(client, make, world)  # Rs 540.00
    assert (await _bill(client, make, world, bill))["can_pay_online"] is True
    page = await _checkout(client, make, world, bill)
    assert page["url"].startswith("https://checkout.stripe.com/")
    assert (page["amount"], page["currency_code"], page["test_mode"]) == ("540.00", "PKR", True)
    assert "4242 4242 4242 4242" in page["note"]
    [params] = stripe_on
    assert (params["amount_minor"], params["currency"]) == (54000, "PKR")
    assert "TEST MODE" in params["description"] and bill["bill_no"] in params["description"]
    assert params["customer_email"] == world.resident.email
    assert params["success_url"].endswith(f"/pay/done.html?bill={bill['id']}")
    assert "payment.checkout_started" in await audit_actions(target_id=bill["id"])
    # Criterion 3: being sent back to the success page changes nothing; only the webhook does.
    assert (await _bill(client, make, world, bill))["status"] == "issued"


async def test_a_signed_webhook_marks_the_bill_paid_once(client, make, world, stripe_on):
    bill = await issued_bill(client, make, world)
    page = await _checkout(client, make, world, bill)
    body, headers = signed(paid_event(page["session_id"], 54000, "PKR"))

    r = await client.post(WEBHOOK, content=body, headers=headers)
    assert r.status_code == 200 and r.json() == {"received": True}
    after = await _bill(client, make, world, bill)
    assert (after["status"], after["amount_paid"]) == ("paid", "540.00")
    [payment] = after["payments"]
    assert payment["method"] == "stripe_test" and payment["amount"] == "540.00"

    # Criterion 2: Stripe retries the same message: still one payment.
    r = await client.post(WEBHOOK, content=body, headers=headers)
    assert r.json() == {"received": True, "duplicate": True}
    # So does the second event Stripe may send for the same payment.
    body2, headers2 = signed(
        paid_event(page["session_id"], 54000, "PKR", "checkout.session.async_payment_succeeded")
    )
    await client.post(WEBHOOK, content=body2, headers=headers2)
    assert len((await _bill(client, make, world, bill))["payments"]) == 1


async def test_bad_signatures_are_rejected_and_logged(client, make, world, stripe_on, caplog):
    """Criterion 1."""
    bill = await issued_bill(client, make, world)
    page = await _checkout(client, make, world, bill)
    event = paid_event(page["session_id"], 54000, "PKR")
    attempts = [
        signed(event, secret="whsec_wrong"),  # signed with another secret
        signed(event, at=int(time.time()) - 3600),  # an old message replayed
        (json.dumps(event).encode(), {"Stripe-Signature": "t=1,v1=00"}),
        (json.dumps(event).encode(), {}),  # no signature at all
    ]
    body, headers = signed(event)
    attempts.append((body.replace(b"54000", b"1"), headers))  # body changed after signing
    with caplog.at_level(logging.WARNING, logger="aquapulse.billing"):
        for body, headers in attempts:
            r = await client.post(WEBHOOK, content=body, headers=headers)
            assert r.status_code == 400, body
    assert caplog.text.count("bad signature") == len(attempts)
    assert (await _bill(client, make, world, bill))["status"] == "issued"


async def test_other_and_unknown_messages_change_nothing(client, make, world, stripe_on):
    bill = await issued_bill(client, make, world)
    page = await _checkout(client, make, world, bill)
    unpaid = paid_event(page["session_id"], 54000, "PKR")
    unpaid["data"]["object"]["payment_status"] = "unpaid"  # e.g. a bank debit on its way
    for event in (
        paid_event(page["session_id"], 54000, "PKR", "customer.created"),
        unpaid,
        paid_event("cs_test_not_ours", 54000, "PKR"),
        paid_event(page["session_id"], 1, "PKR"),  # amount does not match the session
    ):
        body, headers = signed(event)
        assert (await client.post(WEBHOOK, content=body, headers=headers)).status_code == 200
    assert (await _bill(client, make, world, bill))["payments"] == []


async def test_kwd_is_sent_to_stripe_in_fils_rounded_up_to_ten(client, make, stripe_on):
    """Criterion 5: KWD 4.375 -> 4380 fils (Stripe wants the last digit 0)."""
    owner = await make.user("owner")
    building = await make.building(owner, currency_code="KWD", timezone="Asia/Kuwait")
    await add_tariff(building, slabs=[(0, None, "0.875")])  # KWD 0.875 per 1,000 L
    home = await make.home(building)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    device = await make.device(home)
    await use_water(home, device, SEPT, [5000.0 / 30] * 30, building.timezone)

    class W:  # the same shape as the `world` fixture
        pass

    world = W()
    world.owner, world.building, world.resident = owner, building, resident
    await generate(client, make, world)
    [draft] = await bills_of(client, make, world)
    assert (draft["total"], draft["total_text"]) == ("4.375", "KWD 4.375")
    await client.post(f"/api/v1/bills/{draft['id']}/issue", headers=await make.headers(owner))

    page = await _checkout(client, make, world, draft)
    assert (page["amount"], stripe_on[0]["amount_minor"]) == ("4.380", 4380)
    body, headers = signed(paid_event(page["session_id"], 4380, "KWD"))
    await client.post(WEBHOOK, content=body, headers=headers)
    after = await _bill(client, make, world, draft)
    assert (after["status"], after["amount_paid"]) == ("paid", "4.380")


async def test_only_your_own_open_bill_can_be_paid_online(client, make, world, stripe_on):
    bill = await issued_bill(client, make, world)
    stranger = await make.user("resident")
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/checkout", headers=await make.headers(stranger)
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/checkout", headers=await make.headers(world.owner)
    )
    assert r.status_code == 403
    await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "540", "method": "cash"},
        headers=await make.headers(world.owner),
    )
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/checkout", headers=await make.headers(world.resident)
    )
    assert r.status_code == 409


async def test_money_taken_for_a_bill_paid_meanwhile_is_still_recorded(
    client, make, world, stripe_on
):
    """The resident pays online while the owner records cash: Stripe has the money,
    so it is recorded, and the audit log shows how much to refund."""
    bill = await issued_bill(client, make, world)
    page = await _checkout(client, make, world, bill)
    await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "540", "method": "cash"},
        headers=await make.headers(world.owner),
    )
    body, headers = signed(paid_event(page["session_id"], 54000, "PKR"))
    assert (await client.post(WEBHOOK, content=body, headers=headers)).status_code == 200
    after = await _bill(client, make, world, bill)
    assert (after["status"], after["amount_paid"]) == ("paid", "1080.00")
    from sqlalchemy import select

    from app import db
    from app.models import AuditEntry

    async with db.sessions()() as session:
        details = await session.scalar(
            select(AuditEntry.details)
            .where(
                AuditEntry.action == "payment.recorded", AuditEntry.building_id == world.building.id
            )
            .order_by(AuditEntry.id.desc())
            .limit(1)
        )
    assert details["overpaid"] == "540.000"


async def test_the_checkout_request_sent_to_stripe(monkeypatch):
    """What our code hands to the Stripe library (the network call itself is Stripe's)."""
    provider = StripeProvider("sk_test_" + "x" * 24, WEBHOOK_SECRET)
    sent = []

    def fake_create(params):
        sent.append(params)
        return type("Session", (), {"id": "cs_test_123", "url": "https://checkout.stripe.com/x"})()

    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", fake_create)
    page = await provider.create_checkout(
        bill_id="b-1", description="Water bill", amount_minor=4380, currency="KWD",
        customer_email="r@example.com", success_url="https://s", cancel_url="https://c",
    )  # fmt: skip
    assert page == CheckoutPage("cs_test_123", "https://checkout.stripe.com/x")
    [params] = sent
    assert params["mode"] == "payment" and params["metadata"] == {"bill_id": "b-1"}
    assert params["line_items"][0]["price_data"] == {
        "currency": "kwd",
        "unit_amount": 4380,
        "product_data": {"name": "Water bill"},
    }
