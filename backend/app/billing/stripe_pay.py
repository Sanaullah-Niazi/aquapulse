"""Online payment with Stripe Checkout, in TEST mode only (FR-39).

How it works:

1. The resident presses "Pay online". The API asks Stripe for a Checkout
   page for the exact amount still to pay, in the building's currency, and
   sends the resident there. Test card: 4242 4242 4242 4242.
2. After paying, Stripe calls our webhook (POST /api/v1/webhooks/stripe)
   with a message SIGNED with a secret only Stripe and we know.
3. Only that signed message marks the bill paid. Coming back to the
   "success" page proves nothing (anyone can open a URL), so it changes
   nothing (criterion 3).

Payment providers sit behind the small `PaymentProvider` interface, so a
different one can be added later without touching the bill code.

Stripe is optional: with no keys in .env, `provider()` returns None, the
"pay online" option is hidden and manual payments still work (criterion 4).
Live keys (sk_live_...) are refused on purpose: no real money, ever.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import cache
from typing import Protocol

import stripe

from ..config import get_settings

log = logging.getLogger("aquapulse.payments")

TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")
PAID_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")


class InvalidWebhook(Exception):
    """Bad or missing signature: the message did not come from the provider."""


@dataclass(frozen=True)
class CheckoutPage:
    id: str  # Stripe: cs_test_...
    url: str  # where to send the resident


@dataclass(frozen=True)
class PaidCheckout:
    """A verified "this Checkout page was paid" message from the provider."""

    event_id: str
    session_id: str
    amount_minor: int
    currency: str  # upper case, e.g. KWD


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(
        self,
        *,
        bill_id: str,
        description: str,
        amount_minor: int,
        currency: str,
        customer_email: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutPage: ...

    def paid_checkout(self, payload: bytes, signature: str) -> PaidCheckout | None:
        """Verify the signature (InvalidWebhook if wrong). None = an event we ignore."""
        ...


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        self.client = stripe.StripeClient(secret_key)
        self.webhook_secret = webhook_secret

    async def create_checkout(
        self,
        *,
        bill_id: str,
        description: str,
        amount_minor: int,
        currency: str,
        customer_email: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutPage:
        params = {
            "mode": "payment",
            "client_reference_id": bill_id,
            "customer_email": customer_email,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": amount_minor,
                        "product_data": {"name": description},
                    },
                }
            ],
            "metadata": {"bill_id": bill_id},
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        # The Stripe library is not async: run it in a thread so the API keeps serving.
        session = await asyncio.to_thread(self.client.v1.checkout.sessions.create, params)
        return CheckoutPage(session.id, session.url or "")

    def paid_checkout(self, payload: bytes, signature: str) -> PaidCheckout | None:
        try:
            # Checks the signature and that the message is less than 5 minutes old
            # (so an old message cannot be replayed).
            stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as err:
            raise InvalidWebhook(type(err).__name__) from None
        event = json.loads(payload)  # verified: now read it as plain JSON
        if event.get("type") not in PAID_EVENTS:
            return None
        session = event["data"]["object"]
        if session.get("payment_status") != "paid":
            return None  # e.g. a bank payment that is still on its way
        return PaidCheckout(
            event_id=event["id"],
            session_id=session["id"],
            amount_minor=int(session["amount_total"]),
            currency=str(session["currency"]).upper(),
        )


@cache
def provider() -> PaymentProvider | None:
    """The configured provider, or None when online payment is switched off."""
    settings = get_settings()
    key = settings.stripe_secret_key.get_secret_value()
    secret = settings.stripe_webhook_secret.get_secret_value()
    if not key:
        return None
    if not key.startswith(TEST_KEY_PREFIXES):
        log.error("STRIPE_SECRET_KEY is not a TEST key (sk_test_...): online payment is OFF")
        return None
    if not secret.startswith("whsec_"):
        log.error("STRIPE_WEBHOOK_SECRET is missing (whsec_...): online payment is OFF")
        return None
    return StripeProvider(key, secret)
