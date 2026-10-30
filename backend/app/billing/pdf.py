"""PDF bills (FR-36): fill an HTML template, let WeasyPrint print it to A4.

The PDF is made when someone downloads it, from the bill as it is stored, so
it always shows the current payment status. The amounts and lines of an
issued bill never change, so the rest of the PDF is always the same.

WeasyPrint is a free HTML-to-PDF printer written in Python. It needs the
Pango text library: inside Docker it is installed by the Dockerfile; to run
the tests on your Mac, `brew install pango` (see the M6 guide).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Bill, Building, Currency, Occupancy, Payment
from .money import decimals, format_money
from .periods import local_midnight, local_today, month_end

TEMPLATES = Path(__file__).parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), auto_reload=False
)

STATUS_TEXT = {
    "draft": ("DRAFT", "draft"),
    "issued": ("UNPAID", "open"),
    "partially_paid": ("PARTLY PAID", "open"),
    "paid": ("PAID", "paid"),
    "overdue": ("OVERDUE", "overdue"),
    "void": ("VOID", "void"),
}
METHODS = {"cash": "Cash", "bank": "Bank transfer", "stripe_test": "Card (Stripe test)",
           "other": "Other"}  # fmt: skip
CHART_MONTHS = 6
CHART_HEIGHT = 60  # pixels of the tallest bar

MONTHLY_SQL = text(
    "SELECT date_trunc('month', bucket AT TIME ZONE :tz)::date AS month, "
    "SUM(litres)::numeric(14,3) FROM consumption_15m "
    "WHERE home_id = :home AND bucket >= :start AND bucket < :end GROUP BY 1"
)


def _litres(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def _months_back(month: date, count: int) -> list[date]:
    months = [month]
    while len(months) < count:
        months.insert(0, (months[0] - timedelta(days=1)).replace(day=1))
    return months


async def usage_chart(session: AsyncSession, bill: Bill, timezone: str) -> list[dict[str, Any]]:
    """Litres per month for the last 6 months, only from the resident's own
    time in the home (never the previous tenant's water, FR-19)."""
    months = _months_back(bill.month, CHART_MONTHS)
    moved_in = await session.scalar(
        select(func.min(Occupancy.move_in)).where(
            Occupancy.home_id == bill.home_id, Occupancy.user_id == bill.user_id
        )
    )
    start = max(months[0], moved_in or months[0])
    rows = await session.execute(
        MONTHLY_SQL,
        {
            "tz": timezone,
            "home": bill.home_id,
            "start": local_midnight(start, timezone),
            "end": local_midnight(month_end(bill.month) + timedelta(days=1), timezone),
        },
    )
    litres = {month: value for month, value in rows}
    top = max(litres.values(), default=Decimal(0)) or Decimal(1)
    return [
        {
            "label": f"{m:%b}",
            "value": _litres(litres.get(m, Decimal(0))),
            "height": int(litres.get(m, Decimal(0)) * CHART_HEIGHT / top),
        }
        for m in months
    ]


async def bill_context(session: AsyncSession, bill: Bill) -> dict[str, Any]:
    building = await session.get(Building, bill.building_id)
    currency = await session.get(Currency, bill.currency_code)
    assert building is not None and currency is not None

    def money(value: Decimal) -> str:
        return format_money(value, currency.symbol, currency.minor_units)

    def rate(value: Decimal) -> str:  # a rate may have more decimals: $1.234 per 1,000 L
        return format_money(value, currency.symbol, max(currency.minor_units, decimals(value)))

    payments = await session.scalars(
        select(Payment)
        .where(Payment.bill_id == bill.id, Payment.reversed_at.is_(None))
        .order_by(Payment.received_on, Payment.created_at)
    )
    replaces = bill.replaces_bill_id and await session.get(Bill, bill.replaces_bill_id)
    status_text, badge = STATUS_TEXT[bill.status]
    return {
        "building_name": building.name,
        "building_address": building.address,
        "bill_no": bill.bill_no,
        "status_text": status_text,
        "badge": badge,
        "void_reason": bill.void_reason if bill.status == "void" else None,
        "bill_to": bill.bill_to,
        "unit_no": bill.unit_no,
        "period": f"{bill.period_start:%d %b %Y} to {bill.period_end:%d %b %Y}",
        "days": (bill.period_end - bill.period_start).days + 1,
        "month_days": bill.month_days,
        "issued_on": bill.issued_at and local_today(building.timezone, bill.issued_at).isoformat(),
        "due_date": bill.due_date and bill.due_date.isoformat(),
        "reading_start": _litres(bill.reading_start),
        "reading_end": _litres(bill.reading_end),
        "litres": f"{bill.litres:,.0f}",
        "lines": [
            {
                "description": line.description,
                "quantity": "" if line.quantity_l is None else f"{line.quantity_l:,.0f}",
                # Slabs: price per 1,000 L. Taxes and extra charges: a percentage.
                "rate": ""
                if line.rate is None
                else rate(line.rate)
                if line.kind == "slab"
                else f"{line.rate.normalize():f}%",
                "amount": money(line.amount),
            }
            for line in bill.lines
        ],
        "subtotal": money(bill.subtotal),
        "tax_total": money(bill.tax_total),
        "total": money(bill.total),
        "amount_paid": money(bill.amount_paid),
        "outstanding": money(max(bill.total - bill.amount_paid, Decimal(0))),
        "payments": [
            {
                "date": p.received_on.isoformat(),
                "method": METHODS[p.method],
                "reference": p.reference,
                "amount": money(p.amount),
            }
            for p in payments
        ],
        "chart": await usage_chart(session, bill, building.timezone),
        "currency_name": currency.name,
        "currency_code": currency.code,
        "replaces": replaces.bill_no if replaces else None,
    }


def render(context: dict[str, Any]) -> bytes:
    """HTML -> PDF bytes. Slow-ish (a few hundred ms), so call it in a thread."""
    from weasyprint import HTML  # imported here: loading Pango takes a moment

    html = _jinja.get_template("bill.html").render(**context)
    return HTML(string=html, base_url=str(TEMPLATES)).write_pdf()


async def bill_pdf(session: AsyncSession, bill: Bill) -> bytes:
    return await asyncio.to_thread(render, await bill_context(session, bill))
