"""Bills and payments (FR-35 to FR-37, FR-39, FR-40).

Who may do what (SRS section 3.2):
* Owner: generate, issue, void, record and reverse payments, for own buildings.
* System Admin: read only.
* Resident: their OWN bills (never drafts), the PDF, and "pay online".

Like everywhere else, a bill you may not see answers 404, not 403.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi import status as http
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, emailer
from ..billing import lifecycle
from ..billing.generate import BillingError, generate_month
from ..billing.money import decimals, format_money, from_minor, quantum, stripe_minor
from ..billing.pdf import bill_pdf
from ..billing.periods import local_today
from ..billing.stripe_pay import InvalidWebhook, provider
from ..config import get_settings
from ..db import get_session
from ..deps import NOT_FOUND, Caller, client_ip, require, visible_building
from ..models import Bill, Building, CheckoutSession, Currency, Payment, User
from ..schemas import (
    BillLineOut,
    BillOut,
    BillSummaryOut,
    CheckoutOut,
    GenerateIn,
    GenerateOut,
    IssueAllOut,
    PaymentIn,
    PaymentOut,
    ReverseIn,
    UnpaidOut,
    UnpaidTotalOut,
    VoidIn,
)

log = logging.getLogger("aquapulse.billing")

router = APIRouter(tags=["billing: bills and payments"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]
OwnerOrAdmin = Annotated[Caller, Depends(require("owner", "sysadmin"))]
ResidentOnly = Annotated[Caller, Depends(require("resident"))]
AnyRole = Annotated[Caller, Depends(require("owner", "sysadmin", "resident"))]

STATUSES = ("draft", "issued", "partially_paid", "paid", "overdue", "void")
RECEIPT_MAX_BYTES = 2 * 1024 * 1024
# The first bytes of each allowed file type ("magic numbers"): we trust these,
# not the file name or the type the browser claims.
RECEIPT_TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png",
                 b"%PDF-": "application/pdf"}  # fmt: skip


# ----- helpers -----


def _month(value: str) -> date:
    return date.fromisoformat(f"{value}-01")


def _conflict(err: Exception) -> HTTPException:
    return HTTPException(http.HTTP_409_CONFLICT, str(err))


async def _currency(session: AsyncSession, code: str) -> Currency:
    currency = await session.get(Currency, code)
    assert currency is not None  # foreign key
    return currency


def _money(value: Decimal, currency: Currency) -> Decimal:
    """1250.000 -> 1250.00 for a 2-decimal currency (the database keeps 3)."""
    return value.quantize(quantum(currency.minor_units))


def summary_out(bill: Bill, currency: Currency) -> BillSummaryOut:
    return BillSummaryOut(
        id=bill.id,
        bill_no=bill.bill_no,
        building_id=bill.building_id,
        home_id=bill.home_id,
        user_id=bill.user_id,
        bill_to=bill.bill_to,
        unit_no=bill.unit_no,
        month=bill.month,
        period_start=bill.period_start,
        period_end=bill.period_end,
        currency_code=bill.currency_code,
        litres=bill.litres,
        total=_money(bill.total, currency),
        amount_paid=_money(bill.amount_paid, currency),
        outstanding=_money(max(bill.total - bill.amount_paid, Decimal(0)), currency),
        total_text=format_money(bill.total, currency.symbol, currency.minor_units),
        status=bill.status,  # type: ignore[arg-type]
        due_date=bill.due_date,
        issued_at=bill.issued_at,
    )


async def bill_out(session: AsyncSession, bill: Bill) -> BillOut:
    currency = await _currency(session, bill.currency_code)
    payments = await session.scalars(
        select(Payment).where(Payment.bill_id == bill.id).order_by(Payment.created_at)
    )
    return BillOut(
        **summary_out(bill, currency).model_dump(),
        month_days=bill.month_days,
        reading_start=bill.reading_start,
        reading_end=bill.reading_end,
        subtotal=_money(bill.subtotal, currency),
        tax_total=_money(bill.tax_total, currency),
        lines=[
            BillLineOut(
                kind=ln.kind,  # type: ignore[arg-type]
                description=ln.description,
                quantity_l=ln.quantity_l,
                rate=ln.rate.normalize() if ln.rate is not None else None,
                amount=_money(ln.amount, currency),
            )
            for ln in bill.lines
        ],
        payments=[payment_out(p, currency) for p in payments],
        void_reason=bill.void_reason,
        replaces_bill_id=bill.replaces_bill_id,
        shutoff_warned_at=bill.shutoff_warned_at,
        can_pay_online=provider() is not None and bill.status in lifecycle.OPEN,
    )


def payment_out(payment: Payment, currency: Currency) -> PaymentOut:
    return PaymentOut(
        id=payment.id,
        bill_id=payment.bill_id,
        amount=_money(payment.amount, currency),
        method=payment.method,  # type: ignore[arg-type]
        reference=payment.reference,
        received_on=payment.received_on,
        has_receipt=payment.receipt_type is not None,
        created_at=payment.created_at,
        reversed_at=payment.reversed_at,
        reversal_reason=payment.reversal_reason,
    )


async def visible_bill(
    session: AsyncSession, caller: Caller, bill_id: uuid.UUID, *, manage: bool = False
) -> tuple[Bill, Building]:
    """The bill if the caller may see it (manage=True: may change it), else 404."""
    bill = await session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    building = await session.get(Building, bill.building_id)
    assert building is not None
    if caller.role == "resident":
        # Their own bills only, and never a draft (FR-19, FR-21 crit. 2).
        if manage or bill.user_id != caller.id or bill.status == "draft":
            raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    elif caller.role == "sysadmin":
        if manage:
            raise HTTPException(http.HTTP_403_FORBIDDEN, "System Admins can only read bills")
    else:
        await visible_building(session, caller, bill.building_id)
    return bill, building


# ----- generate, list, issue -----


@router.post(
    "/buildings/{building_id}/bills/generate",
    response_model=GenerateOut,
    responses={404: {}, 409: {}},
)
async def generate(building_id: uuid.UUID, body: GenerateIn, session: Session, caller: OwnerOnly):
    """Make the draft bills of a finished month (e.g. "2026-09") from the meter
    readings. Safe to run again: drafts are recalculated, issued bills are kept."""
    building = await visible_building(session, caller, building_id)
    try:
        summary = await generate_month(session, building, _month(body.month), caller)
    except BillingError as err:
        raise _conflict(err) from None
    return GenerateOut(**summary.as_dict())


@router.get(
    "/buildings/{building_id}/bills", response_model=list[BillSummaryOut], responses={404: {}}
)
async def list_bills(
    building_id: uuid.UUID,
    session: Session,
    caller: OwnerOrAdmin,
    month: Annotated[str | None, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")] = None,
    status: Annotated[str | None, Query(pattern="^(" + "|".join(STATUSES) + ")$")] = None,
):
    """The building's bills, newest month first, by unit."""
    building = await visible_building(session, caller, building_id)
    currency = await _currency(session, building.currency_code)
    query = select(Bill).where(Bill.building_id == building.id)
    if month:
        query = query.where(Bill.month == _month(month))
    if status:
        query = query.where(Bill.status == status)
    bills = await session.scalars(
        query.order_by(Bill.month.desc(), Bill.unit_no, Bill.period_start)
    )
    return [summary_out(b, currency) for b in bills]


@router.post(
    "/buildings/{building_id}/bills/issue-all",
    response_model=IssueAllOut,
    responses={404: {}},
)
async def issue_all(building_id: uuid.UUID, body: GenerateIn, session: Session, caller: OwnerOnly):
    """Issue every draft of the month in one go (FR-45 criterion 3)."""
    building = await visible_building(session, caller, building_id)
    currency = await _currency(session, building.currency_code)
    drafts = list(
        await session.scalars(
            select(Bill)
            .where(
                Bill.building_id == building.id,
                Bill.month == _month(body.month),
                Bill.status == "draft",
            )
            .order_by(Bill.unit_no, Bill.period_start)
            .with_for_update()
        )
    )
    for bill in drafts:
        await lifecycle.issue(session, bill, building, caller)
    return IssueAllOut(issued=len(drafts), bills=[summary_out(b, currency) for b in drafts])


@router.get("/me/bills", response_model=list[BillSummaryOut])
async def my_bills(session: Session, caller: ResidentOnly):
    """Your own bills, newest first (drafts are not shown until issued)."""
    bills = list(
        await session.scalars(
            select(Bill)
            .where(Bill.user_id == caller.id, Bill.status != "draft")
            .order_by(Bill.month.desc(), Bill.period_start.desc())
        )
    )
    currencies = {code: await _currency(session, code) for code in {b.currency_code for b in bills}}
    return [summary_out(b, currencies[b.currency_code]) for b in bills]


@router.get("/bills/unpaid", response_model=UnpaidOut, responses={404: {}})
async def unpaid_bills(
    session: Session, caller: OwnerOrAdmin, building_id: uuid.UUID | None = None
):
    """Every bill that still has something to pay, oldest due date first, with
    the totals per currency (FR-38). Owners: all their buildings, or one."""
    query = (
        select(Bill).where(Bill.status.in_(lifecycle.OPEN)).order_by(Bill.due_date, Bill.bill_no)
    )
    if building_id is not None:
        await visible_building(session, caller, building_id)
        query = query.where(Bill.building_id == building_id)
    elif caller.role == "owner":
        query = query.where(
            Bill.building_id.in_(select(Building.id).where(Building.owner_id == caller.id))
        )
    bills = list(await session.scalars(query))
    currencies = {code: await _currency(session, code) for code in {b.currency_code for b in bills}}
    totals: dict[str, UnpaidTotalOut] = {}
    for bill in bills:
        currency = currencies[bill.currency_code]
        total = totals.setdefault(
            currency.code,
            UnpaidTotalOut(
                currency_code=currency.code,
                bills=0,
                amount=Decimal(0),
                overdue_amount=Decimal(0),
                amount_text="",
            ),
        )
        outstanding = bill.total - bill.amount_paid
        total.bills += 1
        total.amount += outstanding
        if bill.status == "overdue":
            total.overdue_amount += outstanding
    for code, total in totals.items():
        currency = currencies[code]
        total.amount_text = format_money(total.amount, currency.symbol, currency.minor_units)
    return UnpaidOut(
        bills=[summary_out(b, currencies[b.currency_code]) for b in bills],
        totals=sorted(totals.values(), key=lambda t: t.currency_code),
    )


@router.get("/bills/{bill_id}", response_model=BillOut, responses={404: {}})
async def get_bill(bill_id: uuid.UUID, session: Session, caller: AnyRole):
    """A bill with its lines and payments."""
    bill, _ = await visible_bill(session, caller, bill_id)
    return await bill_out(session, bill)


@router.get(
    "/bills/{bill_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}, 404: {}},
)
async def get_bill_pdf(bill_id: uuid.UUID, session: Session, caller: AnyRole):
    """The bill as an A4 PDF (FR-36)."""
    bill, building = await visible_bill(session, caller, bill_id)
    pdf = await bill_pdf(session, bill)
    name = bill.bill_no or f"draft-{building.code}-{bill.month:%Y-%m}-{bill.unit_no}"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{name}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/bills/{bill_id}/issue", response_model=BillOut, responses={404: {}, 409: {}})
async def issue_bill(bill_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Issue a draft: it gets its number and due date, and the resident an email."""
    bill, building = await visible_bill(session, caller, bill_id, manage=True)
    try:
        await lifecycle.issue(session, bill, building, caller)
    except lifecycle.BillStateError as err:
        raise _conflict(err) from None
    return await bill_out(session, bill)


@router.post("/bills/{bill_id}/void", response_model=BillOut, responses={404: {}, 409: {}})
async def void_bill(bill_id: uuid.UUID, body: VoidIn, session: Session, caller: OwnerOnly):
    """Cancel a bill, with a reason of 10+ characters. To correct it, generate the
    month again: the new draft will say which bill it replaces."""
    bill, _ = await visible_bill(session, caller, bill_id, manage=True)
    try:
        await lifecycle.void(session, bill, body.reason, caller)
    except lifecycle.BillStateError as err:
        raise _conflict(err) from None
    return await bill_out(session, bill)


# ----- manual payments (FR-40) -----


@router.post(
    "/bills/{bill_id}/payments",
    response_model=PaymentOut,
    status_code=201,
    responses={404: {}, 409: {}, 422: {}},
)
async def record_payment(bill_id: uuid.UUID, body: PaymentIn, session: Session, caller: OwnerOnly):
    """Record cash, a bank transfer or another payment. Less than the amount due
    makes the bill partially paid; more than it is refused."""
    bill, building = await visible_bill(session, caller, bill_id, manage=True)
    currency = await _currency(session, bill.currency_code)
    if decimals(body.amount) > currency.minor_units:
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{currency.code} amounts have at most {currency.minor_units} decimals",
        )
    try:
        payment = await lifecycle.record_payment(
            session,
            bill,
            building,
            amount=body.amount,
            method=body.method,
            received_on=body.received_on or local_today(building.timezone),
            reference=body.reference,
            actor=caller,
        )
    except lifecycle.BillStateError as err:
        raise _conflict(err) from None
    return payment_out(payment, currency)


async def _visible_payment(
    session: AsyncSession, caller: Caller, payment_id: uuid.UUID, *, manage: bool = False
) -> tuple[Payment, Bill, Building]:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    bill, building = await visible_bill(session, caller, payment.bill_id, manage=manage)
    return payment, bill, building


@router.post(
    "/payments/{payment_id}/reverse", response_model=PaymentOut, responses={404: {}, 409: {}}
)
async def reverse_payment(
    payment_id: uuid.UUID, body: ReverseIn, session: Session, caller: OwnerOnly
):
    """Undo a payment recorded by mistake (it is kept, marked reversed). For a
    Stripe payment, refund it in the Stripe dashboard too."""
    payment, bill, building = await _visible_payment(session, caller, payment_id, manage=True)
    try:
        await lifecycle.reverse_payment(session, payment, bill, building, body.reason, caller)
    except lifecycle.BillStateError as err:
        raise _conflict(err) from None
    return payment_out(payment, await _currency(session, bill.currency_code))


@router.put(
    "/payments/{payment_id}/receipt",
    response_model=PaymentOut,
    responses={404: {}, 413: {}, 415: {}},
)
async def upload_receipt(
    payment_id: uuid.UUID,
    session: Session,
    caller: OwnerOnly,
    file: Annotated[UploadFile, File(description="JPEG, PNG or PDF, at most 2 MB")],
):
    """Attach a photo of the receipt (optional, FR-40)."""
    payment, bill, _ = await _visible_payment(session, caller, payment_id, manage=True)
    content = await file.read(RECEIPT_MAX_BYTES + 1)
    if len(content) > RECEIPT_MAX_BYTES:
        raise HTTPException(http.HTTP_413_CONTENT_TOO_LARGE, "The file is larger than 2 MB")
    kind = next((t for magic, t in RECEIPT_TYPES.items() if content.startswith(magic)), None)
    if kind is None:
        raise HTTPException(http.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Use a JPEG, PNG or PDF file")
    payment.receipt, payment.receipt_type = content, kind
    await audit.record(
        session,
        "payment.receipt_added",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="payment",
        target_id=payment.id,
        building_id=bill.building_id,
        details={"type": kind, "bytes": len(content)},
    )
    return payment_out(payment, await _currency(session, bill.currency_code))


@router.get(
    "/payments/{payment_id}/receipt",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}, "image/png": {}, "application/pdf": {}}},
        404: {},
    },
)
async def get_receipt(payment_id: uuid.UUID, session: Session, caller: AnyRole):
    payment, _, _ = await _visible_payment(session, caller, payment_id)
    await session.refresh(payment, ["receipt"])
    if payment.receipt is None or payment.receipt_type is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    return Response(
        payment.receipt,
        media_type=payment.receipt_type,
        # Never let a browser guess another type (an uploaded file must not run as a page).
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


# ----- non-payment warning (FR-27) -----


@router.post(
    "/bills/{bill_id}/shutoff-warning", response_model=BillOut, responses={404: {}, 409: {}}
)
async def shutoff_warning(bill_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Warn the resident that the water may be closed for this overdue bill.
    A shut-off (valve lock with bill_id) is allowed 48 hours later at the
    earliest, and only if the building's policy allows it (FR-27)."""
    bill, building = await visible_bill(session, caller, bill_id, manage=True)
    if not building.nonpayment_shutoff_enabled:
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            "This building does not allow shutting off water for unpaid bills",
        )
    if not lifecycle.is_unpaid_after_due_date(bill, local_today(building.timezone)):
        raise HTTPException(http.HTTP_409_CONFLICT, "The bill is not overdue")
    if bill.shutoff_warned_at is None:
        bill.shutoff_warned_at = datetime.now(UTC)
        user = await session.get(User, bill.user_id)
        currency = await _currency(session, bill.currency_code)
        due = format_money(bill.total - bill.amount_paid, currency.symbol, currency.minor_units)
        sent = bool(
            user
            and await emailer.send_email(
                user.email,
                "Warning: your water supply may be closed",
                f"Hello {bill.bill_to},\n\nYour water bill {bill.bill_no} is overdue since "
                f"{bill.due_date}. Still to pay: {due}.\n\n"
                "If it is not paid, the building owner may close the water supply of your "
                "unit, 48 hours after this message at the earliest.\n"
                "Please pay or contact the owner.\n",
            )
        )
        await lifecycle.log_action(session, "bill.shutoff_warning", bill, caller, {"emailed": sent})
    return await bill_out(session, bill)


# ----- online payment, Stripe test mode (FR-39) -----


@router.post("/bills/{bill_id}/checkout", response_model=CheckoutOut, responses={404: {}, 409: {}})
async def checkout(bill_id: uuid.UUID, session: Session, caller: ResidentOnly):
    """Open a Stripe Checkout page (TEST mode, no real money) for what is still
    to pay. The bill is marked paid only when Stripe's signed webhook arrives."""
    pay = provider()
    if pay is None:
        raise HTTPException(http.HTTP_409_CONFLICT, "Online payment is not set up on this server")
    bill, _ = await visible_bill(session, caller, bill_id)
    if bill.status not in lifecycle.OPEN:
        raise HTTPException(http.HTTP_409_CONFLICT, f"A {bill.status} bill cannot be paid")
    currency = await _currency(session, bill.currency_code)
    amount_minor = stripe_minor(bill.total - bill.amount_paid, currency.minor_units)
    public = get_settings().public_url.rstrip("/")
    page = await pay.create_checkout(
        bill_id=str(bill.id),
        description=f"Water bill {bill.bill_no} ({bill.month:%B %Y}) - TEST MODE, no real money",
        amount_minor=amount_minor,
        currency=currency.code,
        customer_email=caller.user.email,
        success_url=f"{public}/pay/done.html?bill={bill.id}",
        cancel_url=f"{public}/pay/cancelled.html?bill={bill.id}",
    )
    amount = from_minor(amount_minor, currency.minor_units)
    session.add(
        CheckoutSession(
            id=page.id,
            bill_id=bill.id,
            amount=amount,
            amount_minor=amount_minor,
            currency_code=currency.code,
            created_by=caller.id,
        )
    )
    await lifecycle.log_action(
        session,
        "payment.checkout_started",
        bill,
        caller,
        {"amount": str(amount), "session": page.id},
    )
    return CheckoutOut(url=page.url, session_id=page.id, amount=amount, currency_code=currency.code)


@router.post("/webhooks/stripe", responses={400: {}, 404: {}})
async def stripe_webhook(
    request: Request,
    session: Session,
    stripe_signature: Annotated[str, Header(max_length=2000)] = "",
):
    """Called by Stripe (not by apps). Only a correctly SIGNED message counts;
    the same message twice is recorded once."""
    pay = provider()
    if pay is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    payload = await request.body()
    try:
        paid = pay.paid_checkout(payload, stripe_signature)
    except InvalidWebhook as err:
        log.warning("rejected a payment webhook with a bad signature (%s) from %s", err,
                    client_ip(request))  # fmt: skip
        raise HTTPException(http.HTTP_400_BAD_REQUEST, "Invalid signature") from None
    if paid is None:
        return {"received": True}

    # Lock the row: two copies of the same message arriving together are
    # handled one after the other, and the second sees the first's payment.
    checkout_row = await session.scalar(
        select(CheckoutSession).where(CheckoutSession.id == paid.session_id).with_for_update()
    )
    if checkout_row is None:
        log.warning("payment webhook for an unknown checkout session: ignored")
        return {"received": True}
    if (paid.amount_minor, paid.currency) != (
        checkout_row.amount_minor,
        checkout_row.currency_code,
    ):
        log.error("payment webhook amount does not match the checkout session: not recorded")
        return {"received": True}
    if await session.scalar(select(Payment.id).where(Payment.provider_ref == paid.session_id)):
        return {"received": True, "duplicate": True}

    bill = await session.get(Bill, checkout_row.bill_id, with_for_update=True)
    building = bill and await session.get(Building, bill.building_id)
    assert bill is not None and building is not None
    try:
        await lifecycle.record_payment(
            session,
            bill,
            building,
            amount=checkout_row.amount,
            method="stripe_test",
            received_on=local_today(building.timezone),
            reference=f"Stripe test {paid.session_id[-12:]}",
            actor=None,
            provider_ref=paid.session_id,
            allow_overpayment=True,  # the money was taken: it must be on record
            allow_closed=True,
        )
    except IntegrityError:  # pragma: no cover - only with two API servers at once
        return {"received": True, "duplicate": True}
    checkout_row.completed_at = datetime.now(UTC)
    return {"received": True}
