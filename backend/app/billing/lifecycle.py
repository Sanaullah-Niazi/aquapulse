"""What can happen to a bill after it is made (FR-37, FR-40).

    draft ──issue──> issued ──pay part──> partially_paid ──pay rest──> paid
      │                │  └──────────── pay all ─────────────────────────┘
      │                └──after the due date, unpaid──> overdue ──pay──> paid
      └──void──> void    (issued and overdue bills can be voided too)

* An issued bill is never edited. A mistake is fixed by voiding it (with a
  reason) and generating it again: the new bill says which one it replaces.
* Bill numbers are given when a bill is issued, in order per building and
  year, e.g. B01-2026-000123. Drafts have no number, so discarded drafts
  leave no gaps.
* Payments are never deleted, only reversed with a reason. After every
  payment or reversal the status is worked out again from the amounts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, emailer
from .. import events as bus
from ..models import Bill, Building, Currency, Payment, User
from .money import format_money
from .periods import local_today

log = logging.getLogger("aquapulse.billing")

OPEN = ("issued", "partially_paid", "overdue")  # issued and not fully paid
VOIDABLE = ("draft", "issued", "overdue")


class Actor(Protocol):
    """Who did it: a logged-in user (the API's Caller), or None for the worker."""

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def role(self) -> str: ...
    @property
    def ip(self) -> str | None: ...


class BillStateError(Exception):
    """Not allowed in the bill's current state (the API answers 409)."""


async def log_action(
    session: AsyncSession,
    action: str,
    bill: Bill,
    actor: Actor | None,
    details: dict[str, Any] | None = None,
) -> None:
    await audit.record(
        session,
        action,
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else "system",
        ip=actor.ip if actor else None,
        target_type="bill",
        target_id=bill.id,
        building_id=bill.building_id,
        details={"bill_no": bill.bill_no, **(details or {})},
    )


async def announce(session: AsyncSession, bill: Bill) -> None:
    """Tell open apps (WebSocket) that the bill changed (FR-25, FR-50)."""
    await bus.notify(
        session,
        bus.EVENTS,
        {
            "type": "bill",
            "buildingId": bill.building_id,
            "homeId": bill.home_id,
            "userId": bill.user_id,  # residents only hear about their OWN bills
            "billId": bill.id,
            "status": bill.status,
            "amountPaid": bill.amount_paid,
        },
    )


def status_from_amounts(bill: Bill, today: date) -> str:
    """The status of an issued bill, worked out from what was paid and the date."""
    if bill.amount_paid >= bill.total:
        return "paid"
    if bill.due_date is not None and today > bill.due_date:
        return "overdue"
    return "partially_paid" if bill.amount_paid > 0 else "issued"


async def next_bill_no(session: AsyncSession, building: Building, year: int) -> str:
    """B01-2026-000123. One atomic statement, so two owners issuing at the same
    moment can never get the same number."""
    number = await session.scalar(
        text(
            "INSERT INTO bill_counters (building_id, year, last_no) VALUES (:b, :y, 1) "
            "ON CONFLICT (building_id, year) "
            "DO UPDATE SET last_no = bill_counters.last_no + 1 RETURNING last_no"
        ),
        {"b": building.id, "y": year},
    )
    return f"{building.code}-{year}-{number:06d}"


async def issue(session: AsyncSession, bill: Bill, building: Building, actor: Actor | None) -> Bill:
    """draft -> issued: give it a number and a due date, tell the resident."""
    if bill.status != "draft":
        raise BillStateError(f"Only a draft can be issued; this bill is {bill.status}")
    now = datetime.now(UTC)
    today = local_today(building.timezone, now)
    bill.bill_no = await next_bill_no(session, building, today.year)
    bill.due_date = today + timedelta(days=building.due_days)
    bill.issued_at, bill.issued_by = now, actor.id if actor else None
    bill.status = status_from_amounts(bill, today)  # a 0.00 bill is paid at once
    bill.updated_at = now
    await log_action(session, "bill.issued", bill, actor, {"total": str(bill.total)})
    await announce(session, bill)
    await _email_resident(session, bill, building)
    return bill


async def _email_resident(session: AsyncSession, bill: Bill, building: Building) -> None:
    user = await session.get(User, bill.user_id)
    currency = await session.get(Currency, bill.currency_code)
    if user is None or currency is None or not user.is_active:
        return
    amount = format_money(bill.total, currency.symbol, currency.minor_units)
    await emailer.send_email(
        user.email,
        f"Your water bill for {bill.month:%B %Y} is ready",
        f"Hello {bill.bill_to},\n\n"
        f"Your water bill {bill.bill_no} for unit {bill.unit_no} of {building.name} is ready.\n\n"
        f"Water used: {bill.litres:,.0f} litres ({bill.period_start} to {bill.period_end})\n"
        f"Amount: {amount}\n"
        f"Due date: {bill.due_date}\n\n"
        "Open AquaPulse to see the details, download the PDF or pay.\n",
    )


async def void(session: AsyncSession, bill: Bill, reason: str, actor: Actor) -> Bill:
    """Cancel a bill for good, with a reason (FR-37 criterion 2)."""
    if bill.status not in VOIDABLE:
        if bill.status == "partially_paid":
            raise BillStateError("This bill has payments: reverse them first, then void it")
        raise BillStateError(f"A {bill.status} bill cannot be voided")
    if await _active_payments(session, bill.id):
        raise BillStateError("This bill has payments: reverse them first, then void it")
    before = bill.status
    bill.status = "void"
    bill.voided_at, bill.voided_by, bill.void_reason = datetime.now(UTC), actor.id, reason
    bill.updated_at = bill.voided_at
    await log_action(session, "bill.voided", bill, actor, {"reason": reason, "was": before})
    await announce(session, bill)
    return bill


async def _active_payments(session: AsyncSession, bill_id: uuid.UUID) -> Decimal:
    paid = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.bill_id == bill_id, Payment.reversed_at.is_(None)
        )
    )
    return Decimal(paid)


async def refresh_after_payment(session: AsyncSession, bill: Bill, building: Building) -> None:
    """Recalculate amount_paid and the status from the payments that count."""
    bill.amount_paid = await _active_payments(session, bill.id)
    if bill.status not in ("draft", "void"):  # a void bill stays void
        bill.status = status_from_amounts(bill, local_today(building.timezone))
    bill.updated_at = datetime.now(UTC)


async def record_payment(
    session: AsyncSession,
    bill: Bill,
    building: Building,
    *,
    amount: Decimal,
    method: str,
    received_on: date,
    reference: str,
    actor: Actor | None,
    provider_ref: str | None = None,
    allow_overpayment: bool = False,
    allow_closed: bool = False,
) -> Payment:
    """Add a payment (FR-40). Paying the whole outstanding amount -> paid,
    less -> partially_paid. Paying more, or paying a bill that is already
    paid or void, is refused, except for money that Stripe already took (it
    was received, so it must be on record; the audit entry shows how much
    to refund)."""
    if bill.status not in OPEN and not allow_closed:
        raise BillStateError(f"A {bill.status} bill cannot take payments")
    outstanding = bill.total - bill.amount_paid if bill.status != "void" else Decimal(0)
    if amount > outstanding and not allow_overpayment:
        raise BillStateError(f"That is more than the outstanding amount ({outstanding})")
    payment = Payment(
        bill_id=bill.id,
        amount=amount,
        method=method,
        reference=reference,
        provider_ref=provider_ref,
        received_on=received_on,
        recorded_by=actor.id if actor else None,
    )
    session.add(payment)
    await session.flush()
    await refresh_after_payment(session, bill, building)
    await audit.record(
        session,
        "payment.recorded",
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else "system",
        ip=actor.ip if actor else None,
        target_type="payment",
        target_id=payment.id,
        building_id=bill.building_id,
        details={
            "bill": str(bill.id),
            "bill_no": bill.bill_no,
            "amount": str(amount),
            "method": method,
            "status": bill.status,
            **({"overpaid": str(amount - outstanding)} if amount > outstanding else {}),
        },
    )
    await announce(session, bill)
    return payment


async def reverse_payment(
    session: AsyncSession,
    payment: Payment,
    bill: Bill,
    building: Building,
    reason: str,
    actor: Actor,
) -> Payment:
    """Undo a payment that was recorded by mistake. It stays on record."""
    if payment.reversed_at is not None:
        raise BillStateError("This payment is already reversed")
    payment.reversed_at, payment.reversed_by = datetime.now(UTC), actor.id
    payment.reversal_reason = reason
    await session.flush()
    await refresh_after_payment(session, bill, building)
    await audit.record(
        session,
        "payment.reversed",
        actor_id=actor.id,
        actor_role=actor.role,
        ip=actor.ip,
        target_type="payment",
        target_id=payment.id,
        building_id=bill.building_id,
        details={"bill": str(bill.id), "amount": str(payment.amount), "reason": reason,
                 "status": bill.status},
    )  # fmt: skip
    await announce(session, bill)
    return payment


async def mark_overdue(
    session: AsyncSession, building: Building, now: datetime | None = None
) -> int:
    """Bills past their due date and not fully paid become overdue (run by the worker).

    Reminder emails before and after the due date: reminders.py (FR-38)."""
    today = local_today(building.timezone, now)
    result = await session.execute(
        update(Bill)
        .where(
            Bill.building_id == building.id,
            Bill.status.in_(("issued", "partially_paid")),
            Bill.due_date < today,
        )
        .values(status="overdue", updated_at=func.now())
        .returning(Bill.id)
    )
    ids = list(result.scalars())
    for bill in await session.scalars(
        select(Bill).where(Bill.id.in_(ids)).execution_options(populate_existing=True)
    ):
        await log_action(session, "bill.overdue", bill, None, {"due_date": str(bill.due_date)})
        await announce(session, bill)
    return len(ids)


SHUTOFF_WARNING = timedelta(hours=48)


def is_unpaid_after_due_date(bill: Bill, today: date) -> bool:
    """FR-27: only such a bill can justify a non-payment shut-off."""
    return (
        bill.status in OPEN
        and bill.due_date is not None
        and today > bill.due_date
        and bill.amount_paid < bill.total
    )


def shutoff_problem(
    bill: Bill | None, building: Building, home_id: uuid.UUID, now: datetime
) -> str | None:
    """Why the water may NOT be closed for this unpaid bill, or None if it may
    (FR-27 criterion 4). Water is an essential service, so all three are needed:
    the building's policy is on, the bill is overdue, and the resident was
    warned at least 48 hours ago."""
    if not building.nonpayment_shutoff_enabled:
        return "This building does not allow shutting off water for unpaid bills"
    if bill is None or bill.home_id != home_id:
        return "That bill does not belong to this home"
    if not is_unpaid_after_due_date(bill, local_today(building.timezone, now)):
        return "The bill is not overdue"
    if bill.shutoff_warned_at is None:
        return "The resident has not been warned yet (POST /bills/{id}/shutoff-warning)"
    if now - bill.shutoff_warned_at < SHUTOFF_WARNING:
        ready = bill.shutoff_warned_at + SHUTOFF_WARNING
        return (
            f"The warning was sent less than 48 hours ago; allowed from {ready:%Y-%m-%d %H:%M} UTC"
        )
    return None
