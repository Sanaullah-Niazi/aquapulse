"""The bill calculation engine (FR-34): litres in, line items out.

A pure function: no database, no clock, no network. The same input always
gives the same bill, which is what makes it easy to test by hand-calculated
examples (tests/billing/test_bill_calculation_engine.py) and to reuse for
the owner's "example bill" preview and, later, the AI bill forecast (FR-65).

How a bill is calculated, in simple words:

1. **Slabs.** The litres are split over the tariff's bands. With slabs
   0-5,000 L at Rs 60 and 5,000-15,000 L at Rs 120 (prices per 1,000 L),
   7,000 L = 5,000 L x 60/1000 + 2,000 L x 120/1000 = Rs 300 + Rs 240.
2. **Part months.** A resident who lived there 15 of 30 days gets slabs
   half as big (0-2,500 L, 2,500-7,500 L ...), so moving mid-month is fair
   (FR-35). The same happens when the tariff changes mid-month: each
   tariff "part" gets its own days, litres and slab sizes (FR-33 crit. 4).
3. **Extra charges** set by the owner: a percentage of the water charge
   (e.g. "Sewerage 10%") or a fixed amount (prorated by days the same way).
   There is no built-in fixed fee: only what the owner adds.
4. **Taxes**: a percentage of water + extra charges.

Every line is rounded half up to the currency's minor unit, and the total
is the sum of the rounded lines, so the lines on the PDF always add up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .money import ZERO, decimals, round_money

LITRE = Decimal("0.001")  # litres are kept to the millilitre
KL = Decimal(1000)
HUNDRED = Decimal(100)
MAX_SLABS = 10
MAX_CHARGES = 10

ChargeKind = Literal["tax_percent", "extra_percent", "extra_fixed"]
LineKind = Literal["slab", "extra", "tax"]


class TariffError(ValueError):
    """The tariff breaks a rule (FR-33 criteria 1 and 2)."""


@dataclass(frozen=True)
class Slab:
    from_l: int
    to_l: int | None  # None = no upper limit (only the last slab)
    rate_per_kl: Decimal  # price per 1,000 litres, in the building's currency


@dataclass(frozen=True)
class Charge:
    name: str
    kind: ChargeKind
    value: Decimal  # a percentage (0 to 100) or a fixed amount


@dataclass(frozen=True)
class Tariff:
    slabs: tuple[Slab, ...]
    charges: tuple[Charge, ...] = ()
    label: str = ""  # e.g. "Tariff v2"; shown on lines when a bill has two parts


@dataclass(frozen=True)
class Part:
    """The days of a bill that one tariff version covers."""

    tariff: Tariff
    litres: Decimal
    days: int


@dataclass(frozen=True)
class Line:
    kind: LineKind
    description: str
    amount: Decimal
    quantity_l: Decimal | None = None
    rate: Decimal | None = None
    part: int = 0  # which Part (0 = first) the line belongs to


@dataclass(frozen=True)
class Result:
    litres: Decimal
    lines: tuple[Line, ...] = field(default_factory=tuple)
    subtotal: Decimal = ZERO  # water + extra charges
    tax_total: Decimal = ZERO
    total: Decimal = ZERO


def validate(tariff: Tariff, minor_units: int) -> None:
    """Raise TariffError if the tariff is not usable (FR-33 criteria 1 and 2)."""
    slabs = tariff.slabs
    if not 1 <= len(slabs) <= MAX_SLABS:
        raise TariffError(f"A tariff needs 1 to {MAX_SLABS} slabs")
    if slabs[0].from_l != 0:
        raise TariffError("The first slab must start at 0 litres")
    for n, (slab, following) in enumerate(zip(slabs, (*slabs[1:], None), strict=True), 1):
        if slab.rate_per_kl < 0:
            raise TariffError(f"Slab {n}: the rate cannot be negative")
        if decimals(slab.rate_per_kl) > 3:
            raise TariffError(f"Slab {n}: use at most 3 decimals for the rate")
        if following is None:
            if slab.to_l is not None:
                raise TariffError("The last slab must have no upper limit")
        elif slab.to_l is None:
            raise TariffError(f"Slab {n}: only the last slab can have no upper limit")
        elif slab.to_l <= slab.from_l:
            raise TariffError(f"Slab {n}: 'to' must be more than 'from'")
        elif following.from_l != slab.to_l:
            raise TariffError(
                f"Slab {n + 1} must start where slab {n} ends ({slab.to_l} L): "
                "no gaps and no overlaps"
            )
    if len(tariff.charges) > MAX_CHARGES:
        raise TariffError(f"At most {MAX_CHARGES} taxes and extra charges")
    for charge in tariff.charges:
        if charge.value < 0:
            raise TariffError(f"{charge.name}: cannot be negative")
        if charge.kind == "extra_fixed":
            if decimals(charge.value) > minor_units:
                raise TariffError(f"{charge.name}: at most {minor_units} decimals")
        elif charge.value > HUNDRED:
            raise TariffError(f"{charge.name}: a percentage must be 0 to 100")
        elif decimals(charge.value) > 3:
            raise TariffError(f"{charge.name}: use at most 3 decimals")


def _scaled(limit: int, days: int, month_days: int) -> Decimal:
    """A slab limit for part of a month: 5,000 L x 15/30 = 2,500 L."""
    return (Decimal(limit) * days / month_days).quantize(LITRE, rounding=ROUND_HALF_UP)


def _litres_text(value: Decimal) -> str:
    return f"{value.normalize():,f}"


def _percent_text(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def calculate(parts: list[Part], month_days: int, minor_units: int) -> Result:
    """Calculate a bill. `month_days` = days in the calendar month (28 to 31)."""
    if not parts:
        zero = round_money(ZERO, minor_units)
        return Result(litres=ZERO.quantize(LITRE), subtotal=zero, tax_total=zero, total=zero)
    lines: list[Line] = []
    several = len(parts) > 1
    for index, part in enumerate(parts):
        prefix = f"{part.tariff.label}: " if several and part.tariff.label else ""
        prorated = part.days != month_days
        litres = part.litres.quantize(LITRE, rounding=ROUND_HALF_UP)

        # 1. Slabs: the litres that fall into each band.
        water = ZERO
        for n, slab in enumerate(part.tariff.slabs, 1):
            low = _scaled(slab.from_l, part.days, month_days)
            high = None if slab.to_l is None else _scaled(slab.to_l, part.days, month_days)
            used = (
                max(ZERO, litres - low)
                if high is None
                else min(max(ZERO, litres - low), high - low)
            )
            if used == 0:
                continue
            band = (
                f"{_litres_text(low)}+ L"
                if high is None
                else f"{_litres_text(low)}-{_litres_text(high)} L"
            )
            note = f", {part.days}/{month_days} days" if prorated else ""
            amount = round_money(used * slab.rate_per_kl / KL, minor_units)
            water += amount
            lines.append(
                Line(
                    "slab",
                    f"{prefix}Slab {n} ({band}{note})",
                    amount,
                    used,
                    slab.rate_per_kl,
                    index,
                )
            )

        # 2. Extra charges set by the owner.
        extras = ZERO
        for charge in part.tariff.charges:
            if charge.kind == "extra_percent":
                amount = round_money(water * charge.value / HUNDRED, minor_units)
                text = f"{prefix}{charge.name} ({_percent_text(charge.value)} of water)"
            elif charge.kind == "extra_fixed":
                amount = round_money(charge.value * part.days / month_days, minor_units)
                note = f" x {part.days}/{month_days} days" if prorated else ""
                text = f"{prefix}{charge.name} (fixed{note})"
            else:
                continue
            extras += amount
            # `rate` holds a percentage; a fixed charge has none (its amount says it all).
            rate = charge.value if charge.kind == "extra_percent" else None
            lines.append(Line("extra", text, amount, rate=rate, part=index))

        # 3. Taxes on water + extra charges.
        for charge in part.tariff.charges:
            if charge.kind == "tax_percent":
                amount = round_money((water + extras) * charge.value / HUNDRED, minor_units)
                text = f"{prefix}{charge.name} ({_percent_text(charge.value)})"
                lines.append(Line("tax", text, amount, rate=charge.value, part=index))

    # Sums of already-rounded lines; round_money only fixes the number of decimals.
    tax_total = round_money(sum((ln.amount for ln in lines if ln.kind == "tax"), ZERO), minor_units)
    subtotal = round_money(sum((ln.amount for ln in lines if ln.kind != "tax"), ZERO), minor_units)
    return Result(
        litres=sum((p.litres.quantize(LITRE, rounding=ROUND_HALF_UP) for p in parts), ZERO),
        lines=tuple(lines),
        subtotal=subtotal,
        tax_total=tax_total,
        total=subtotal + tax_total,
    )
