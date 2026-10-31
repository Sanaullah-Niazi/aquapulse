"""The logged-in user's own account."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

import segno
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import Settings, get_settings
from ..db import get_session
from ..deps import Caller, active_occupancy_filter, current_caller
from ..models import Building, Occupancy, RecoveryCode
from ..schemas import (
    ChangePasswordIn,
    MeOut,
    MePatch,
    MessageOut,
    RecoveryCodesOut,
    TwoFactorCodeIn,
    TwoFactorDisableIn,
    TwoFactorSetupIn,
    TwoFactorSetupOut,
)
from ..security import totp
from ..security.passwords import hash_password, password_problem, verify_password
from .auth import revoke_all_sessions

router = APIRouter(prefix="/me", tags=["me"])
Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[Caller, Depends(current_caller)]


async def _me(session: AsyncSession, caller: Caller) -> MeOut:
    out = MeOut.model_validate(caller.user)
    out.two_factor = caller.user.totp_enabled_at is not None
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


# ----- two-factor login (FR-70) -----
# 1. setup: a new secret, shown as a QR code (not switched on yet)
# 2. enable: the first code from the app proves it works; 10 recovery codes
# 3. disable: needs the password AND a code, so a stolen session cannot do it

Config = Annotated[Settings, Depends(get_settings)]


def _check_password(caller: Caller, password: str) -> None:
    if not verify_password(caller.user.password_hash, password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is wrong")


@router.post("/2fa/setup", response_model=TwoFactorSetupOut, responses={400: {}, 409: {}})
async def two_factor_setup(body: TwoFactorSetupIn, caller: Me, settings: Config):
    """Step 1: a new secret for the authenticator app. Nothing changes until step 2."""
    if caller.user.totp_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor login is already on")
    _check_password(caller, body.password)
    secret = totp.new_secret()
    caller.user.totp_secret = totp.encrypt(settings, secret)
    url = totp.otpauth_url(secret, caller.user.email)
    svg = segno.make(url, error="m").svg_inline(scale=5, dark="#000", light="#fff")
    return TwoFactorSetupOut(secret=secret, otpauth_url=url, qr_svg=svg)


async def _new_codes(session: AsyncSession, caller: Caller, settings: Settings) -> list[str]:
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == caller.id))
    codes = totp.new_recovery_codes()
    session.add_all(
        RecoveryCode(user_id=caller.id, code_hash=totp.recovery_hashes(settings, c)[0])
        for c in codes
    )
    return codes


def _check_code(caller: Caller, settings: Settings, code: str) -> None:
    secret = totp.decrypt(settings, caller.user.totp_secret or "")
    step = totp.matching_step(secret, code, caller.user.totp_last_step)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Wrong code. Check the phone's time.")
    caller.user.totp_last_step = step


@router.post("/2fa/enable", response_model=RecoveryCodesOut, responses={400: {}, 409: {}})
async def two_factor_enable(body: TwoFactorCodeIn, session: Session, caller: Me, settings: Config):
    """Step 2: type the code the app shows. Returns 10 recovery codes, shown only now."""
    if caller.user.totp_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor login is already on")
    if caller.user.totp_secret is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start with /me/2fa/setup")
    _check_code(caller, settings, body.code)
    caller.user.totp_enabled_at = datetime.now(UTC)
    codes = await _new_codes(session, caller, settings)
    await audit.record(session, "auth.2fa_enabled", actor_id=caller.id, actor_role=caller.role,
                       ip=caller.ip)  # fmt: skip
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesOut, responses={400: {}, 409: {}})
async def two_factor_new_codes(
    body: TwoFactorDisableIn, session: Session, caller: Me, settings: Config
):
    """10 new recovery codes (the old ones stop working). Needs password and code."""
    if caller.user.totp_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor login is off")
    _check_password(caller, body.password)
    _check_code(caller, settings, body.code)
    codes = await _new_codes(session, caller, settings)
    await audit.record(session, "auth.2fa_new_recovery_codes", actor_id=caller.id,
                       actor_role=caller.role, ip=caller.ip)  # fmt: skip
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/2fa/disable", response_model=MessageOut, responses={400: {}, 409: {}})
async def two_factor_disable(
    body: TwoFactorDisableIn, session: Session, caller: Me, settings: Config
):
    """Switch two-factor login off. Needs the password and a current code."""
    if caller.user.totp_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor login is off")
    _check_password(caller, body.password)
    _check_code(caller, settings, body.code)
    caller.user.totp_enabled_at = None
    caller.user.totp_secret = None
    caller.user.totp_last_step = None
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == caller.id))
    await audit.record(session, "auth.2fa_disabled", actor_id=caller.id, actor_role=caller.role,
                       ip=caller.ip)  # fmt: skip
    return MessageOut(message="Two-factor login is off.")
