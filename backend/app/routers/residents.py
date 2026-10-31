"""Residents: invitations and occupancies (FR-18, FR-21)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import Settings, get_settings
from ..db import get_session
from ..deps import Caller, require, visible_home
from ..emailer import send_email
from ..models import Building, Invitation, Occupancy, User
from ..schemas import InvitationIn, InvitationOut, OccupancyOut, OccupancyPatch
from ..security.tokens import new_opaque_token

router = APIRouter(tags=["residents"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]


async def _has_billing_contact(session: AsyncSession, home_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Occupancy.home_id == home_id,
                    Occupancy.is_billing_contact.is_(True),
                    Occupancy.move_out.is_(None),
                )
            )
        )
    )


@router.post(
    "/homes/{home_id}/invitations",
    response_model=InvitationOut,
    status_code=201,
    responses={404: {}, 409: {}},
)
async def invite_resident(
    home_id: uuid.UUID,
    body: InvitationIn,
    session: Session,
    caller: OwnerOnly,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Email a one-time code (valid 48 h) that lets the resident join this home."""
    home = await visible_home(session, caller, home_id, manage=True)
    if home.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This home is archived")
    if body.is_billing_contact and await _has_billing_contact(session, home.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This home already has a billing contact")
    token, token_hash = new_opaque_token()
    invite = Invitation(
        home_id=home.id,
        email=body.email.lower(),
        full_name=body.full_name,
        move_in=body.move_in,
        is_billing_contact=body.is_billing_contact,
        token_hash=token_hash,
        invited_by=caller.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.invitation_hours),
    )
    session.add(invite)
    await session.flush()
    building = await session.get(Building, home.building_id)
    sent = await send_email(
        invite.email,
        f"You're invited to AquaPulse ({building.name if building else ''}, unit {home.unit_no})",
        f"Hello {invite.full_name},\n\n"
        f"Your building uses AquaPulse to show your water use and bills.\n"
        f"Use this code within {settings.invitation_hours} hours to create your account:\n\n"
        f"    {token}\n\n"
        f"Or open this link: {settings.web_app_url}/accept-invite?token={token}\n",
    )
    await audit.record(
        session,
        "resident.invited",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="home",
        target_id=home.id,
        building_id=home.building_id,
        details={"invitation": str(invite.id), "billing_contact": invite.is_billing_contact},
    )
    return InvitationOut(
        id=invite.id,
        home_id=home.id,
        email=invite.email,
        expires_at=invite.expires_at,
        email_sent=sent,
    )


@router.get("/homes/{home_id}/occupancies", response_model=list[OccupancyOut], responses={404: {}})
async def list_occupancies(home_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Who lived here and when (newest first), with their name and email."""
    home = await visible_home(session, caller, home_id, manage=True)
    rows = await session.execute(
        select(Occupancy, User.full_name, User.email)
        .join(User, User.id == Occupancy.user_id)
        .where(Occupancy.home_id == home.id)
        .order_by(Occupancy.move_in.desc())
    )
    return [
        OccupancyOut.model_validate(occupancy).model_copy(
            update={"full_name": name, "email": email}
        )
        for occupancy, name, email in rows
    ]


@router.patch(
    "/occupancies/{occupancy_id}",
    response_model=OccupancyOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def update_occupancy(
    occupancy_id: uuid.UUID, body: OccupancyPatch, session: Session, caller: OwnerOnly
):
    """Set a move-out date, or choose the billing contact."""
    occupancy = await session.get(Occupancy, occupancy_id)
    if occupancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    home = await visible_home(session, caller, occupancy.home_id, manage=True)
    patch = body.model_dump(exclude_unset=True)
    if patch.get("move_out") is not None and patch["move_out"] < occupancy.move_in:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "move_out is before move_in")
    before = {k: str(getattr(occupancy, k)) for k in patch}
    for field, value in patch.items():
        setattr(occupancy, field, value)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This home already has a billing contact"
        ) from None
    await audit.record(
        session,
        "resident.occupancy_updated",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="occupancy",
        target_id=occupancy.id,
        building_id=home.building_id,
        details={"before": before, "after": {k: str(v) for k, v in patch.items()}},
    )
    return occupancy
