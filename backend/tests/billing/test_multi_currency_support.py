"""FR-32: 11 currencies, 2 or 3 decimals, never floats. Also FR-20 criterion 2."""

from __future__ import annotations

import ast
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.billing.money import (
    decimals,
    format_money,
    from_minor,
    round_money,
    stripe_minor,
    to_minor,
)

from .conftest import issued_bill

pytestmark = pytest.mark.usefixtures("outbox")

BILLING_CODE = Path(__file__).resolve().parents[2] / "app" / "billing"


def test_amounts_are_shown_the_currencys_way():
    """Criterion 1."""
    assert format_money(D("1250"), "Rs", 2) == "Rs 1,250.00"
    assert format_money(D("4.375"), "KWD", 3) == "KWD 4.375"
    assert format_money(D("12.5"), "$", 2) == "$12.50"
    assert format_money(D("1234567.891"), "£", 2) == "£1,234,567.89"
    assert format_money(D("-5"), "A$", 2) == "-A$5.00"
    assert format_money(D("0"), "OMR", 3) == "OMR 0.000"


def test_rounding_and_minor_units():
    assert round_money(D("4.3745"), 3) == D("4.375")
    assert round_money(D("0.005"), 2) == D("0.01")  # half up
    assert (to_minor(D("12.50"), 2), to_minor(D("4.375"), 3)) == (1250, 4375)
    assert (str(from_minor(1250, 2)), str(from_minor(4380, 3))) == ("12.50", "4.380")
    assert (decimals(D("4.375")), decimals(D("60")), decimals(D("60.00"))) == (3, 0, 0)


def test_stripe_amounts_for_three_decimal_currencies_end_in_zero():
    """FR-39 criterion 5: rounded UP to 10 fils, so a bill is never left a few fils short."""
    assert stripe_minor(D("4.375"), 3) == 4380
    assert stripe_minor(D("4.370"), 3) == 4370
    assert stripe_minor(D("0.001"), 3) == 10
    assert stripe_minor(D("540.00"), 2) == 54000


async def test_the_eleven_currencies_are_in_one_table(client, make):
    """Criterion 3: the currencies live in one table; the API checks codes against it."""
    from sqlalchemy import select

    from app import db
    from app.models import Currency

    async with db.sessions()() as session:
        rows = {c.code: c.minor_units for c in await session.scalars(select(Currency))}
    assert rows == {
        "PKR": 2, "USD": 2, "GBP": 2, "EUR": 2, "AUD": 2, "AED": 2,
        "SAR": 2, "QAR": 2, "KWD": 3, "BHD": 3, "OMR": 3,
    }  # fmt: skip
    owner = await make.user("owner")
    headers = await make.headers(owner)
    r = await client.post(
        "/api/v1/buildings", json={"name": "Muscat", "currency_code": "OMR"}, headers=headers
    )
    assert r.status_code == 201 and r.json()["currency_code"] == "OMR"
    assert r.json()["code"].startswith("B")
    r = await client.post(
        "/api/v1/buildings", json={"name": "X", "currency_code": "XYZ"}, headers=headers
    )
    assert r.status_code == 422


def _floats_in(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"{path.name}:{node.lineno} float literal {node.value}")
        if isinstance(node, ast.Name) and node.id == "float":
            found.append(f"{path.name}:{node.lineno} uses float")
    return found


def test_no_floats_anywhere_in_billing_code():
    """Criterion 2: money is Decimal only. This test is the 'lint rule'."""
    files = sorted(BILLING_CODE.glob("*.py"))
    assert len(files) >= 7
    problems = [p for f in files for p in _floats_in(f)]
    assert problems == []


def test_the_float_check_would_catch_one(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("total = float(amount) * 1.5\n")
    assert len(_floats_in(bad)) == 2


async def test_currency_is_locked_once_bills_exist(client, make, world):
    """FR-20 criterion 2: never mix currencies in one building's bills."""
    url = f"/api/v1/buildings/{world.building.id}"
    headers = await make.headers(world.owner)
    # No bills yet: changing is fine (and back).
    assert (
        await client.patch(url, json={"currency_code": "USD"}, headers=headers)
    ).status_code == 200
    assert (
        await client.patch(url, json={"currency_code": "PKR"}, headers=headers)
    ).status_code == 200
    await issued_bill(client, make, world)
    r = await client.patch(url, json={"currency_code": "USD"}, headers=headers)
    assert r.status_code == 409 and "cannot change" in r.json()["detail"]
    # Sending the same currency again is not a change.
    assert (
        await client.patch(url, json={"currency_code": "PKR"}, headers=headers)
    ).status_code == 200
