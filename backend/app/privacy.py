"""Privacy (FR-75): a resident can download their data and delete their account.

Download: everything AquaPulse keeps about the person, in one JSON file.
That covers their profile, the homes they lived in, their bills (with their
lines), payments, and daily water use while they lived there.

Delete: the account is anonymised, not removed. Name, email and phone are
replaced, logins and phones are cut off, and their messages are deleted. The
bills keep their amounts, because a building's accounts must still add up.
After that, the person's name and email are nowhere in the database
(test_privacy_data_minimisation_export_and_deletion.py checks every table).

The 13-month limit for raw readings is a TimescaleDB retention policy
(migration 0011).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    Bill,
    Building,
    Home,
    Invitation,
    Notification,
    Occupancy,
    PasswordReset,
    Payment,
    PushDevice,
    RecoveryCode,
    RefreshToken,
    User,
)

DELETED_NAME = "Deleted resident"
EXPORT_VERSION = 1

DAILY_SQL = text(
    """
    SELECT (time_bucket(INTERVAL '1 day', bucket, CAST(:tz AS text)) AT TIME ZONE :tz)::date AS day,
           round(sum(litres)::numeric, 2) AS litres
    FROM consumption_15m
    WHERE home_id = :home AND bucket >= :start AND bucket < :end
    GROUP BY 1 ORDER BY 1
    """
)


def deleted_email(user_id: uuid.UUID) -> str:
    # ".invalid" can never be a real address (RFC 2606), and the ID keeps it unique.
    return f"deleted-{user_id}@deleted.invalid"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _row(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: _plain(getattr(obj, f)) for f in fields}


async def export_data(session: AsyncSession, user: User) -> dict[str, Any]:
    """Everything AquaPulse keeps about this person (FR-75 AC1)."""
    now = datetime.now(UTC)
    profile = _row(
        user,
        ["id", "email", "full_name", "phone", "role", "created_at", "alert_email_min",
         "push_min", "quiet_from", "quiet_until"],
    )  # fmt: skip
    profile["email_muted_rules"] = user.email_muted_rules
    profile["push_muted_rules"] = user.push_muted_rules
    profile["two_factor"] = user.totp_enabled_at is not None

    occupancies = []
    rows = await session.execute(
        select(Occupancy, Home, Building)
        .join(Home, Home.id == Occupancy.home_id)
        .join(Building, Building.id == Home.building_id)
        .where(Occupancy.user_id == user.id)
        .order_by(Occupancy.move_in)
    )
    for occ, home, building in rows:
        start = datetime.combine(occ.move_in, datetime.min.time()).replace(tzinfo=UTC)
        end = (
            datetime.combine(occ.move_out, datetime.max.time()).replace(tzinfo=UTC)
            if occ.move_out
            else now
        )
        daily = await session.execute(
            DAILY_SQL, {"tz": building.timezone, "home": home.id, "start": start, "end": end}
        )
        occupancies.append(
            {
                "building": building.name,
                "unit_no": home.unit_no,
                "move_in": _plain(occ.move_in),
                "move_out": _plain(occ.move_out),
                "billing_contact": occ.is_billing_contact,
                "daily_litres": [{"day": _plain(r.day), "litres": str(r.litres)} for r in daily],
            }
        )

    bills = []
    for bill in await session.scalars(
        select(Bill)
        .where(Bill.user_id == user.id)
        .options(selectinload(Bill.lines), selectinload(Bill.payments))
        .order_by(Bill.month)
    ):
        out = _row(
            bill,
            ["bill_no", "bill_to", "unit_no", "month", "period_start", "period_end",
             "currency_code", "litres", "subtotal", "tax_total", "total", "amount_paid",
             "status", "due_date", "issued_at"],
        )  # fmt: skip
        out["lines"] = [
            _row(line, ["description", "quantity_l", "rate", "amount"]) for line in bill.lines
        ]
        out["payments"] = [
            _row(p, ["amount", "method", "reference", "received_on", "reversed_at"])
            for p in bill.payments
        ]
        bills.append(out)

    return {
        "format": "aquapulse-personal-data",
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(),
        "profile": profile,
        "occupancies": occupancies,
        "bills": bills,
        "note": "Raw meter readings older than 13 months are deleted automatically; "
        "daily totals are kept.",
    }


async def delete_account(session: AsyncSession, user: User) -> None:
    """Anonymise the account (FR-75 AC2). The caller checks the password and
    writes the audit entry; everything happens in one transaction."""
    old_email, old_name = user.email, user.full_name
    today = date.today()

    # Free text that an owner may have typed with the person's name in it.
    bill_ids = select(Bill.id).where(Bill.user_id == user.id).scalar_subquery()
    home_ids = select(Occupancy.home_id).where(Occupancy.user_id == user.id).scalar_subquery()
    for column, where in (
        (Payment.reference, Payment.bill_id.in_(bill_ids)),
        (Home.notes, Home.id.in_(home_ids)),
    ):
        model = column.class_
        for needle in {old_name, old_email}:
            if needle:
                await session.execute(
                    update(model)
                    .where(where)
                    .values({column.key: func.replace(column, needle, DELETED_NAME)})
                    .execution_options(synchronize_session=False)
                )

    await session.execute(update(Bill).where(Bill.user_id == user.id).values(bill_to=DELETED_NAME))
    await session.execute(
        update(Invitation)
        .where(func.lower(Invitation.email) == old_email.lower())
        .values(email=deleted_email(user.id), full_name=DELETED_NAME,
                used_at=func.coalesce(Invitation.used_at, func.now()))
    )  # fmt: skip
    await session.execute(
        update(Occupancy)
        .where(Occupancy.user_id == user.id, Occupancy.move_out.is_(None))
        .values(move_out=func.greatest(Occupancy.move_in, today))
    )
    for model in (Notification, PushDevice, RecoveryCode, PasswordReset, RefreshToken):
        await session.execute(delete(model).where(model.user_id == user.id))

    user.email = deleted_email(user.id)
    user.full_name = DELETED_NAME
    user.phone = None
    user.password_hash = None
    user.is_active = False
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.quiet_from = None
    user.quiet_until = None
    user.email_muted_rules = []
    user.push_muted_rules = []
    await session.flush()
