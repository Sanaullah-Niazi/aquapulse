"""System Admin: owner accounts, the full audit log and its check (FR-20, FR-28,
FR-55, FR-73), and emails that could not be delivered (FR-60)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..alerts.store import audit_signal_saved
from ..db import get_session
from ..deps import Caller, require
from ..models import AuditCheck, AuditEntry, Notification, User
from ..schemas import (
    AuditOut,
    AuditVerifyOut,
    NotificationOut,
    OwnerIn,
    OwnerPatch,
    UserOut,
)
from ..security.passwords import hash_password, password_problem
from .auth import revoke_all_sessions

router = APIRouter(prefix="/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[Caller, Depends(require("sysadmin"))]


@router.get("/owners", response_model=list[UserOut])
async def list_owners(session: Session, caller: Admin):
    return list(
        await session.scalars(select(User).where(User.role == "owner").order_by(User.email))
    )


@router.post("/owners", response_model=UserOut, status_code=201, responses={409: {}, 422: {}})
async def create_owner(body: OwnerIn, session: Session, caller: Admin):
    """Create a building owner account with a first password (they can change it)."""
    email = body.email.lower()
    problem = password_problem(body.password, email)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    owner = User(
        email=email,
        full_name=body.full_name,
        role="owner",
        password_hash=hash_password(body.password),
    )
    session.add(owner)
    await session.flush()
    await audit.record(
        session,
        "admin.owner_created",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="user",
        target_id=owner.id,
    )
    return owner


@router.patch("/owners/{owner_id}", response_model=UserOut, responses={404: {}})
async def update_owner(owner_id: uuid.UUID, body: OwnerPatch, session: Session, caller: Admin):
    """Rename, disable or re-enable an owner. Disabling logs them out everywhere."""
    owner = await session.get(User, owner_id)
    if owner is None or owner.role != "owner":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(owner, k) for k in changes}
    for field, value in changes.items():
        setattr(owner, field, value)
    if changes.get("is_active") is False:
        await revoke_all_sessions(session, owner.id)
    await audit.record(
        session,
        "admin.owner_updated",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="user",
        target_id=owner.id,
        details={"before": before, "after": changes},
    )
    return owner


def audit_query(
    since: datetime | None, until: datetime | None, actor_id: uuid.UUID | None, action: str | None
):
    query = select(AuditEntry).order_by(AuditEntry.id.desc())
    if since:
        query = query.where(AuditEntry.at >= since)
    if until:
        query = query.where(AuditEntry.at < until)
    if actor_id:
        query = query.where(AuditEntry.actor_id == actor_id)
    if action:
        query = query.where(AuditEntry.action == action)
    return query


@router.get("/audit", response_model=list[AuditOut])
async def all_audit(
    session: Session,
    caller: Admin,
    since: datetime | None = None,
    until: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The whole audit log, newest first, with filters."""
    query = audit_query(since, until, actor_id, action).limit(limit).offset(offset)
    return list(await session.scalars(query))


@router.post("/audit/verify", response_model=AuditVerifyOut)
async def verify_audit(session: Session, caller: Admin):
    """Re-calculate every hash now: proves no audit entry was changed or removed.
    The worker also does this every day; GET /admin/audit/checks lists the results."""
    check = await audit.check_chain(session, caller.id)
    await audit_signal_saved(session, check, datetime.now(UTC))
    return check


@router.get("/audit/checks", response_model=list[AuditVerifyOut])
async def audit_checks(
    session: Session, caller: Admin, limit: Annotated[int, Query(ge=1, le=100)] = 30
):
    """The latest audit chain checks, newest first (daily and on demand)."""
    return list(
        await session.scalars(select(AuditCheck).order_by(AuditCheck.at.desc()).limit(limit))
    )


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(
    session: Session,
    caller: Admin,
    status_: Annotated[str, Query(alias="status", pattern="^(pending|sent|failed)$")] = "failed",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Emails by status, newest first. "failed" = not delivered after 3 retries (FR-60)."""
    query = (
        select(Notification)
        .where(Notification.status == status_)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(query))
