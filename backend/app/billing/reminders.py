"""Bill reminders (FR-38): 3 days before the due date, on the due date, and
7 days after it, for bills that still have something to pay.

Run by the worker every minute. A row in bill_reminders is written together
with the queued email, and (bill, kind) is its key, so each reminder is sent
once only, however often the job runs (criterion 2). If the worker was down,
it catches up: e.g. a "due today" reminder is still sent the day after, but
never a "3 days before" reminder once the due date has passed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Bill, BillReminder, Building, Currency, Notification, User
from .lifecycle import OPEN
from .money import format_money
from .periods import local_today

BEFORE = timedelta(days=3)
AFTER = timedelta(days=7)


def reminder_due(due: date, today: date) -> str | None:
    """Which reminder fits today (None = none)."""
    if due - BEFORE <= today < due:
        return "before_due"
    if due <= today < due + AFTER:
        return "due_today"
    if today >= due + AFTER:
        return "after_due"
    return None


SUBJECTS = {
    "before_due": "Reminder: your water bill {no} is due on {due}",
    "due_today": "Your water bill {no} is due",
    "after_due": "Your water bill {no} is overdue",
}


async def send_reminders(session: AsyncSession, building: Building, now: datetime) -> int:
    """Queue the reminders that are due in this building. Returns how many."""
    today = local_today(building.timezone, now)
    bills = await session.scalars(
        select(Bill).where(
            Bill.building_id == building.id,
            Bill.status.in_(OPEN),
            Bill.due_date.is_not(None),
            Bill.due_date <= today + BEFORE,
        )
    )
    sent = 0
    for bill in bills:
        assert bill.due_date is not None
        kind = reminder_due(bill.due_date, today)
        if kind is None:
            continue
        # The key (bill, kind): a second run inserts nothing, so sends nothing.
        new = await session.scalar(
            insert(BillReminder)
            .values(bill_id=bill.id, kind=kind)
            .on_conflict_do_nothing()
            .returning(BillReminder.kind)
        )
        if new is None:
            continue
        user = await session.get(User, bill.user_id)
        currency = await session.get(Currency, bill.currency_code)
        if user is None or currency is None or not user.is_active:
            continue
        still_to_pay = format_money(bill.total - bill.amount_paid, currency.symbol,
                                    currency.minor_units)  # fmt: skip
        session.add(
            Notification(
                user_id=user.id,
                channel="email",
                kind="bill_reminder",
                bill_id=bill.id,
                subject=SUBJECTS[kind].format(no=bill.bill_no, due=bill.due_date),
                body=(
                    f"Hello {bill.bill_to},\n\n"
                    f"Water bill {bill.bill_no} for unit {bill.unit_no} of {building.name} "
                    f"({bill.month:%B %Y}) was due on {bill.due_date}.\n"
                    if kind == "after_due"
                    else f"Hello {bill.bill_to},\n\n"
                    f"Water bill {bill.bill_no} for unit {bill.unit_no} of {building.name} "
                    f"({bill.month:%B %Y}) is due on {bill.due_date}.\n"
                )
                + f"Still to pay: {still_to_pay}\n\n"
                "Open AquaPulse to see the bill, download the PDF or pay. "
                "If you have already paid, please ignore this email.\n",
                next_attempt_at=now,
            )
        )
        sent += 1
    return sent
