"""The logged-in user's own account."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db import get_session
from ..deps import Caller, active_occupancy_filter, current_caller
from ..models import Building, Occupancy
from ..schemas import ChangePasswordIn, MeOut, MePatch, MessageOut
from ..security.passwords import hash_password, password_problem, verify_password
from .auth import revoke_all_sessions

router = APIRouter(prefix="/me", tags=["me"])
Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[Caller, Depends(current_caller)]


async def _me(session: AsyncSession, caller: Caller) -> MeOut:
    out = MeOut.model_validate(caller.user)
    if caller.role == "owner":
        out.building_ids = list(
            await session.scalars(select(Building.id).where(Building.owner_id == caller.id))
        )
    elif caller.role == "resident":
        out.home_ids = list(
            await session.scalars(
                select(Occupancy.home_id).where(*active_occupancy_filter(caller.id, date.today()))
            )
        )
    return out


@router.get("", response_model=MeOut)
async def get_me(session: Session, caller: Me):
    """Who am I? Includes my buildings (owner) or my current home(s) (resident)."""
    return await _me(session, caller)


@router.patch("", response_model=MeOut)
async def update_me(body: MePatch, session: Session, caller: Me):
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(caller.user, field, value)
    await audit.record(
        session,
        "user.profile_updated",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="user",
        target_id=caller.id,
        details={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return await _me(session, caller)


@router.post("/password", response_model=MessageOut, responses={400: {}, 422: {}})
async def change_password(body: ChangePasswordIn, session: Session, caller: Me):
    """Change my password. Every other session is logged out, this one too: log in again."""
    if not verify_password(caller.user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    problem = password_problem(body.new_password, caller.user.email)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    caller.user.password_hash = hash_password(body.new_password)
    await revoke_all_sessions(session, caller.id)
    await audit.record(
        session, "auth.password_changed", actor_id=caller.id, actor_role=caller.role, ip=caller.ip
    )
    return MessageOut(message="Password changed. Please log in again.")
