"""Who is calling, and may they? (FR-19)

Two layers, both on every request:
1. Role check: `require("owner", "sysadmin")` → 403 for any other role.
2. Object check: `visible_building` / `visible_home` → 404 when the object
   exists but is not yours. 404 (not 403) means a resident who changes an ID
   in the URL cannot even learn whether that home exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_session
from .models import Building, Home, Occupancy, RefreshToken, User
from .security.ratelimit import SlidingWindow
from .security.tokens import TokenError, decode_access_token

# Swagger's "Authorize" button logs in through this form endpoint.
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

api_limiter = SlidingWindow(limit=120, window_s=60)  # FR-72: 120 requests/min per user


@dataclass(frozen=True)
class Caller:
    user: User
    session_id: uuid.UUID
    ip: str | None

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role


def client_ip(request: Request) -> str | None:
    # Behind Caddy, uvicorn's --proxy-headers already put the real client here.
    return request.client.host if request.client else None


def _unauthorized(detail: str = "Not logged in or session expired") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def current_caller(
    request: Request,
    token: Annotated[str | None, Depends(oauth2)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Caller:
    if not token:
        raise _unauthorized()
    try:
        claims = decode_access_token(settings, token)
        user_id, sid = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (TokenError, ValueError):
        raise _unauthorized() from None

    user = await session.get(User, user_id)
    if user is None or not await session_alive(session, user, sid):
        raise _unauthorized()

    key = str(user.id)
    if api_limiter.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests, slow down",
            headers={"Retry-After": str(api_limiter.retry_after(key))},
        )
    api_limiter.hit(key)
    return Caller(user, sid, client_ip(request))


async def session_alive(session: AsyncSession, user: User, sid: uuid.UUID) -> bool:
    """The account is active and this login session was not logged out.

    After logout (or theft detection) the session has no live refresh token
    left, so its access tokens stop working at once, not 15 minutes later.
    The WebSocket checks this again every 30 seconds (FR-25)."""
    if not user.is_active:
        return False
    return bool(
        await session.scalar(
            select(
                exists().where(
                    RefreshToken.family_id == sid,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > datetime.now(UTC),
                )
            )
        )
    )


TWO_FACTOR_FIRST = (
    "System Admins must turn on two-factor login first (Profile, then Two-factor login)"
)


def require(*roles: str):
    """Dependency factory: only these roles may call the endpoint (else 403).

    FR-70: a System Admin without two-factor login gets 403 here too, with the
    header `X-Two-Factor: setup-required`, so the app sends them to set it up.
    Only their own account (/me, /me/2fa/...) and logging out still work."""

    async def checker(caller: Annotated[Caller, Depends(current_caller)]) -> Caller:
        if caller.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role may not do this")
        if caller.role == "sysadmin" and caller.user.totp_enabled_at is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                TWO_FACTOR_FIRST,
                headers={"X-Two-Factor": "setup-required"},
            )
        return caller

    return checker


NOT_FOUND = "Not found"


async def visible_building(
    session: AsyncSession, caller: Caller, building_id: uuid.UUID
) -> Building:
    building = await session.get(Building, building_id)
    if building is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if caller.role == "sysadmin" or (caller.role == "owner" and building.owner_id == caller.id):
        return building
    raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)


def active_occupancy_filter(user_id: uuid.UUID, today: date):
    return (
        Occupancy.user_id == user_id,
        Occupancy.move_in <= today,
        or_(Occupancy.move_out.is_(None), Occupancy.move_out >= today),
    )


async def visible_home(
    session: AsyncSession, caller: Caller, home_id: uuid.UUID, *, manage: bool = False
) -> Home:
    """manage=True: only the building's owner or a System Admin (edit, invite ...)."""
    home = await session.get(Home, home_id)
    if home is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if caller.role == "sysadmin":
        return home
    if caller.role == "owner":
        building = await session.get(Building, home.building_id)
        if building is not None and building.owner_id == caller.id:
            return home
    elif caller.role == "resident" and not manage and home.archived_at is None:
        lives_here = await session.scalar(
            select(
                exists().where(
                    Occupancy.home_id == home.id,
                    *active_occupancy_filter(caller.id, date.today()),
                )
            )
        )
        if lives_here:
            return home
    raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)


async def resident_since(session: AsyncSession, caller: Caller, home: Home) -> date | None:
    """For a resident: the move-in day of their current stay in this home.
    Residents only see readings from their own time there, never the
    previous tenant's (FR-19). None for owners and admins (no limit)."""
    if caller.role != "resident":
        return None
    return await session.scalar(
        select(Occupancy.move_in)
        .where(Occupancy.home_id == home.id, *active_occupancy_filter(caller.id, date.today()))
        .order_by(Occupancy.move_in.desc())
        .limit(1)
    )
