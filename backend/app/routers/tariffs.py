"""Tariffs (FR-33) and the example-bill preview (FR-34, FR-45).

A tariff is never edited: saving a change creates the next VERSION with its
own start date. Bills store their own lines, so issued bills keep what they
were calculated with, whatever happens to the tariff later.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..billing import engine
from ..billing.money import format_money
from ..db import get_session
from ..deps import NOT_FOUND, Caller, require, visible_building
from ..models import Bill, Currency, Tariff, TariffCharge, TariffSlab
from ..schemas import BillLineOut, PreviewOut, TariffIn, TariffOut, TariffPreviewIn, TariffRules

router = APIRouter(tags=["billing: tariffs"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]


async def currency_or_422(session: AsyncSession, code: str) -> Currency:
    currency = await session.get(Currency, code)
    if currency is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown currency {code}")
    return currency


def checked_rules(body: TariffRules, minor_units: int) -> engine.Tariff:
    """The request as an engine tariff, or 422 with the rule it breaks."""
    tariff = engine.Tariff(
        slabs=tuple(engine.Slab(s.from_l, s.to_l, s.rate_per_kl) for s in body.slabs),
        charges=tuple(engine.Charge(c.name, c.kind, c.value) for c in body.charges),
    )
    try:
        engine.validate(tariff, minor_units)
    except engine.TariffError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(err)) from None
    return tariff


@router.get("/buildings/{building_id}/tariffs", response_model=list[TariffOut], responses={404: {}})
async def list_tariffs(building_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Every version, newest first."""
    await visible_building(session, caller, building_id)
    return list(
        await session.scalars(
            select(Tariff).where(Tariff.building_id == building_id).order_by(Tariff.version.desc())
        )
    )


@router.post(
    "/buildings/{building_id}/tariffs",
    response_model=TariffOut,
    status_code=201,
    responses={404: {}, 409: {}, 422: {}},
)
async def create_tariff(
    building_id: uuid.UUID, body: TariffIn, session: Session, caller: OwnerOnly
):
    """Save a new tariff version. Slabs must start at 0, follow each other
    with no gap or overlap, and the last one has no upper limit (to_l null).
    Rates are per 1,000 litres in the building's currency."""
    building = await visible_building(session, caller, building_id)
    currency = await currency_or_422(session, building.currency_code)
    checked_rules(body, currency.minor_units)
    # Issued bills are final: a new version may not reach back into a month
    # that is already billed (void those bills first if they were wrong).
    last_billed = await session.scalar(
        select(func.max(Bill.period_end)).where(
            Bill.building_id == building.id, Bill.status.not_in(("draft", "void"))
        )
    )
    if last_billed is not None and body.effective_from <= last_billed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Bills up to {last_billed} are already issued: the new tariff must start after "
            "that date (or void those bills first)",
        )
    version = (
        await session.scalar(
            select(func.max(Tariff.version)).where(Tariff.building_id == building.id)
        )
        or 0
    ) + 1
    tariff = Tariff(
        building_id=building.id,
        name=body.name,
        version=version,
        effective_from=body.effective_from,
        currency_code=building.currency_code,
        created_by=caller.id,
        slabs=[
            TariffSlab(position=n, from_l=s.from_l, to_l=s.to_l, rate_per_kl=s.rate_per_kl)
            for n, s in enumerate(body.slabs)
        ],
        charges=[
            TariffCharge(position=n, name=c.name, kind=c.kind, value=c.value)
            for n, c in enumerate(body.charges)
        ],
    )
    session.add(tariff)
    await session.flush()
    await audit.record(
        session,
        "tariff.created",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="tariff",
        target_id=tariff.id,
        building_id=building.id,
        details={
            "version": version,
            "effective_from": body.effective_from.isoformat(),
            "currency": building.currency_code,
            "slabs": [[s.from_l, s.to_l, str(s.rate_per_kl)] for s in body.slabs],
            "charges": [[c.name, c.kind, str(c.value)] for c in body.charges],
        },
    )
    return tariff


@router.get("/tariffs/{tariff_id}", response_model=TariffOut, responses={404: {}})
async def get_tariff(tariff_id: uuid.UUID, session: Session, caller: OwnerOnly):
    tariff = await session.get(Tariff, tariff_id)
    if tariff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await visible_building(session, caller, tariff.building_id)
    return tariff


@router.post("/tariffs/preview", response_model=PreviewOut, responses={422: {}})
async def preview(body: TariffPreviewIn, session: Session, caller: OwnerOnly):
    """An example bill for a tariff that is not saved yet, calculated by the
    same engine as real bills, so the preview always matches (FR-45)."""
    currency = await currency_or_422(session, body.currency_code)
    tariff = checked_rules(body, currency.minor_units)
    if body.days > body.month_days:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "days cannot be more than month_days"
        )
    result = engine.calculate(
        [engine.Part(tariff, body.litres, body.days)], body.month_days, currency.minor_units
    )
    return PreviewOut(
        currency_code=currency.code,
        litres=result.litres,
        lines=[
            BillLineOut(
                kind=ln.kind,
                description=ln.description,
                quantity_l=ln.quantity_l,
                rate=ln.rate,
                amount=ln.amount,
            )
            for ln in result.lines
        ],
        subtotal=result.subtotal,
        tax_total=result.tax_total,
        total=result.total,
        total_text=format_money(result.total, currency.symbol, currency.minor_units),
    )
