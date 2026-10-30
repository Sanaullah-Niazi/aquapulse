"""Buildings and homes (FR-20). Owners see only their own buildings (FR-19)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db import get_session
from ..deps import Caller, require, visible_building, visible_home
from ..models import AuditEntry, Building, Home, User
from ..schemas import (
    AuditOut,
    BuildingIn,
    BuildingOut,
    BuildingPatch,
    HomeIn,
    HomeOut,
    HomePatch,
)
from .admin import audit_query

router = APIRouter(tags=["buildings and homes"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOrAdmin = Annotated[Caller, Depends(require("owner", "sysadmin"))]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]
AnyRole = Annotated[Caller, Depends(require("owner", "sysadmin", "resident"))]


def _changes(obj, patch: dict) -> dict:
    """Only fields that really change, as {"field": [old, new]} for the audit log."""
    return {k: [str(getattr(obj, k)), str(v)] for k, v in patch.items() if getattr(obj, k) != v}


# ----- buildings -----


@router.get("/buildings", response_model=list[BuildingOut])
async def list_buildings(session: Session, caller: OwnerOrAdmin):
    query = select(Building).order_by(Building.name)
    if caller.role == "owner":
        query = query.where(Building.owner_id == caller.id)
    return list(await session.scalars(query))


async def _check_owner(session: AsyncSession, owner_id: uuid.UUID) -> None:
    owner = await session.get(User, owner_id)
    if owner is None or owner.role != "owner" or not owner.is_active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "owner_id is not an active owner"
        )


@router.post("/buildings", response_model=BuildingOut, status_code=201, responses={422: {}})
async def create_building(body: BuildingIn, session: Session, caller: OwnerOrAdmin):
    """Owners create buildings for themselves; a System Admin must say which owner."""
    if caller.role == "owner":
        if body.owner_id not in (None, caller.id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Owners create buildings for themselves")
        owner_id = caller.id
    else:
        if body.owner_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "owner_id is required")
        await _check_owner(session, body.owner_id)
        owner_id = body.owner_id
    building = Building(**body.model_dump(exclude={"owner_id"}), owner_id=owner_id)
    session.add(building)
    await session.flush()
    await audit.record(
        session,
        "building.created",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="building",
        target_id=building.id,
        building_id=building.id,
        details={"name": building.name, "currency": building.currency_code},
    )
    return building


@router.get("/buildings/{building_id}", response_model=BuildingOut, responses={404: {}})
async def get_building(building_id: uuid.UUID, session: Session, caller: OwnerOrAdmin):
    return await visible_building(session, caller, building_id)


@router.patch("/buildings/{building_id}", response_model=BuildingOut, responses={404: {}})
async def update_building(
    building_id: uuid.UUID, body: BuildingPatch, session: Session, caller: OwnerOrAdmin
):
    """Edit name, address, time zone, currency or due days. Only a System Admin can
    move a building to another owner."""
    building = await visible_building(session, caller, building_id)
    patch = body.model_dump(exclude_unset=True)
    if "owner_id" in patch:
        if caller.role != "sysadmin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a System Admin can change owners")
        await _check_owner(session, patch["owner_id"])
    changes = _changes(building, patch)
    for field, value in patch.items():
        setattr(building, field, value)
    await audit.record(
        session,
        "building.updated",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="building",
        target_id=building.id,
        building_id=building.id,
        details=changes,
    )
    return building


@router.get("/buildings/{building_id}/audit", response_model=list[AuditOut], responses={404: {}})
async def building_audit(
    building_id: uuid.UUID,
    session: Session,
    caller: OwnerOnly,
    since: datetime | None = None,
    until: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Audit entries about this building and its homes, newest first (FR-28)."""
    await visible_building(session, caller, building_id)
    query = audit_query(since, until, actor_id, action).where(AuditEntry.building_id == building_id)
    return list(await session.scalars(query.limit(limit).offset(offset)))


# ----- homes -----


@router.get("/buildings/{building_id}/homes", response_model=list[HomeOut], responses={404: {}})
async def list_homes(
    building_id: uuid.UUID,
    session: Session,
    caller: OwnerOrAdmin,
    include_archived: bool = False,
):
    await visible_building(session, caller, building_id)
    query = select(Home).where(Home.building_id == building_id).order_by(Home.unit_no)
    if not include_archived:
        query = query.where(Home.archived_at.is_(None))
    return list(await session.scalars(query))


@router.post(
    "/buildings/{building_id}/homes",
    response_model=HomeOut,
    status_code=201,
    responses={404: {}, 409: {}},
)
async def create_home(building_id: uuid.UUID, body: HomeIn, session: Session, caller: OwnerOrAdmin):
    await visible_building(session, caller, building_id)
    home = Home(building_id=building_id, **body.model_dump())
    session.add(home)
    await _flush_unique(session)
    await audit.record(
        session,
        "home.created",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="home",
        target_id=home.id,
        building_id=building_id,
        details={"unit_no": home.unit_no},
    )
    return home


async def _flush_unique(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This unit number already exists in the building"
        ) from None


@router.get("/homes/{home_id}", response_model=HomeOut, responses={404: {}})
async def get_home(home_id: uuid.UUID, session: Session, caller: AnyRole):
    """Owners and admins see any home they manage; residents only the home they live in."""
    return await visible_home(session, caller, home_id)


@router.patch("/homes/{home_id}", response_model=HomeOut, responses={404: {}, 409: {}})
async def update_home(home_id: uuid.UUID, body: HomePatch, session: Session, caller: OwnerOrAdmin):
    home = await visible_home(session, caller, home_id, manage=True)
    patch = body.model_dump(exclude_unset=True)
    changes = _changes(home, patch)
    for field, value in patch.items():
        setattr(home, field, value)
    await _flush_unique(session)
    await audit.record(
        session,
        "home.updated",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="home",
        target_id=home.id,
        building_id=home.building_id,
        details=changes,
    )
    return home


@router.delete("/homes/{home_id}", response_model=HomeOut, responses={404: {}})
async def archive_home(home_id: uuid.UUID, session: Session, caller: OwnerOrAdmin):
    """Archive a home (soft delete). Nothing is erased, so billing history stays (FR-20)."""
    home = await visible_home(session, caller, home_id, manage=True)
    if home.archived_at is None:
        home.archived_at = datetime.now(UTC)
        await audit.record(
            session,
            "home.archived",
            actor_id=caller.id,
            actor_role=caller.role,
            ip=caller.ip,
            target_type="home",
            target_id=home.id,
            building_id=home.building_id,
        )
    return home
