"""Money in a currency's minor unit (FR-32).

Every currency has a number of decimals, its "minor unit" (ISO 4217): 2 for
most (100 paisa = 1 rupee), 3 for the Kuwaiti, Bahraini and Omani dinar/rial
(1,000 fils = 1 dinar). The list of currencies lives in ONE place, the
`currencies` table (migration 0001): adding a currency = adding one row.

Amounts are Python `Decimal`, never float, and are rounded "half up"
(0.005 -> 0.01), the way people round by hand.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

ZERO = Decimal(0)


def quantum(minor_units: int) -> Decimal:
    """The smallest amount: 0.01 for 2 decimals, 0.001 for 3."""
    return Decimal(1).scaleb(-minor_units)


def round_money(amount: Decimal, minor_units: int) -> Decimal:
    """Round half up to the currency's minor unit: 4.3745 KWD -> 4.375."""
    return amount.quantize(quantum(minor_units), rounding=ROUND_HALF_UP)


def decimals(value: Decimal) -> int:
    """How many decimals a number was written with: 4.375 -> 3, 60 -> 0."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def format_money(amount: Decimal, symbol: str, minor_units: int) -> str:
    """'Rs 1,250.00', 'KWD 4.375', '$12.50': thousands separators, the
    currency's decimals, and a space after symbols made of letters."""
    number = f"{round_money(amount, minor_units):,.{minor_units}f}"
    if number.startswith("-"):
        return "-" + format_money(-amount, symbol, minor_units)
    space = " " if symbol[-1].isalpha() else ""
    return f"{symbol}{space}{number}"


def to_minor(amount: Decimal, minor_units: int) -> int:
    """Amount in the smallest unit, as payment services want it: Rs 12.50 -> 1250."""
    return int(round_money(amount, minor_units).scaleb(minor_units))


def from_minor(value: int, minor_units: int) -> Decimal:
    """1250 -> 12.50 (2 decimals); 4380 -> 4.380 (3 decimals)."""
    return Decimal(value).scaleb(-minor_units).quantize(quantum(minor_units))


def stripe_minor(amount: Decimal, minor_units: int) -> int:
    """The amount for Stripe (FR-39 criterion 5).

    Stripe takes 3-decimal currencies (KWD, BHD, OMR) in fils, but the last
    digit must be 0. We round UP to the next 10 fils, so a bill is never left
    with a few fils unpaid: KWD 4.375 -> 4380 (= KWD 4.380, 5 fils extra).
    """
    minor = to_minor(amount, minor_units)
    if minor_units == 3:
        return int((Decimal(minor) / 10).to_integral_value(rounding=ROUND_CEILING)) * 10
    return minor
