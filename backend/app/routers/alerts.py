"""Alerts, the owner's alert rules, each person's email and push choice, and
the phones that get push notifications (FR-58 to FR-62, M9).

Who sees which alert (FR-19):
* Owner: alerts of their own buildings.
* Resident: alerts about their own home, from their move-in day on (never
  the previous tenant's, never tank or building alerts).
* System Admin: everything, read only, plus "system" alerts such as a broken
  audit chain, which only they can acknowledge and resolve.

An alert you may not see answers 404, like everywhere else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http
from sqlalchemy import and_, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, push
from .. import events as bus
from ..alerts.rules import AUDIT_RULE, RULES, check, describe, settings_for
from ..alerts.store import alert_event
from ..db import get_session
from ..deps import NOT_FOUND, Caller, active_occupancy_filter, require, visible_building
from ..models import OPEN_ALERT_STATES, Alert, Building, Occupancy, PushDevice
from ..schemas import (
    AlertOut,
    AlertResolveIn,
    AlertRulesIn,
    AlertRulesOut,
    NotificationPrefsIn,
    NotificationPrefsOut,
    PushDeviceIn,
    PushForgetIn,
)

router = APIRouter(tags=["alerts"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
AnyRole = Annotated[Caller, Depends(require("owner", "sysadmin", "resident"))]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]
OwnerOrAdmin = Annotated[Caller, Depends(require("owner", "sysadmin"))]

SEVERITY = Annotated[str | None, Query(pattern="^(info|warning|critical)$")]


def _resident_can_see(user_id: uuid.UUID):
    """A resident's homes, and only alerts from their move-in day on."""
    return exists().where(
        Occupancy.home_id == Alert.home_id,
        Occupancy.move_in <= Alert.opened_at,
        *active_occupancy_filter(user_id, date.today()),
    )


async def visible_alert(session: AsyncSession, caller: Caller, alert_id: uuid.UUID) -> Alert:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)
    if caller.role == "sysadmin":
        return alert
    if caller.role == "owner" and alert.building_id is not None:
        building = await session.get(Building, alert.building_id)
        if building is not None and building.owner_id == caller.id:
            return alert
    if caller.role == "resident" and alert.home_id is not None:
        allowed = await session.scalar(
            select(_resident_can_see(caller.id)).where(Alert.id == alert.id).select_from(Alert)
        )
        if allowed:
            return alert
    raise HTTPException(http.HTTP_404_NOT_FOUND, NOT_FOUND)


def _may_change(caller: Caller, alert: Alert) -> None:
    """System Admins only change system alerts (separation of duties)."""
    if caller.role == "sysadmin" and alert.building_id is not None:
        raise HTTPException(http.HTTP_403_FORBIDDEN, "System Admins can only read building alerts")


async def _log(session: AsyncSession, caller: Caller, action: str, alert: Alert, **details):
    await audit.record(
        session,
        action,
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="alert",
        target_id=alert.id,
        building_id=alert.building_id,
        details={"rule": alert.rule, "severity": alert.severity, **details},
    )


# ----- alerts -----


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    session: Session,
    caller: AnyRole,
    active: bool | None = Query(None, description="true: open or acknowledged; false: closed"),
    severity: SEVERITY = None,
    building_id: uuid.UUID | None = None,
    home_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Alerts you may see, newest first."""
    query = select(Alert).order_by(Alert.opened_at.desc()).limit(limit).offset(offset)
    if caller.role == "owner":
        mine = select(Building.id).where(Building.owner_id == caller.id)
        query = query.where(Alert.building_id.in_(mine))
    elif caller.role == "resident":
        query = query.where(and_(Alert.home_id.is_not(None), _resident_can_see(caller.id)))
    if active is not None:
        states = Alert.state.in_(OPEN_ALERT_STATES)
        query = query.where(states if active else ~states)
    if severity:
        query = query.where(Alert.severity == severity)
    if building_id:
        query = query.where(Alert.building_id == building_id)
    if home_id:
        query = query.where(Alert.home_id == home_id)
    return list(await session.scalars(query))


@router.get("/alerts/{alert_id}", response_model=AlertOut, responses={404: {}})
async def get_alert(alert_id: uuid.UUID, session: Session, caller: AnyRole):
    return await visible_alert(session, caller, alert_id)


@router.post(
    "/alerts/{alert_id}/acknowledge", response_model=AlertOut, responses={404: {}, 409: {}}
)
async def acknowledge(alert_id: uuid.UUID, session: Session, caller: AnyRole):
    """ "I have seen it." Records who and when (FR-59 criterion 3). The alert
    stays until it is resolved, or the problem is gone for 10 minutes."""
    alert = await visible_alert(session, caller, alert_id)
    _may_change(caller, alert)
    if alert.state != "open":
        raise HTTPException(
            http.HTTP_409_CONFLICT, f"An {alert.state} alert cannot be acknowledged"
        )
    alert.state = "acknowledged"
    alert.acknowledged_at, alert.acknowledged_by = datetime.now(UTC), caller.id
    await _log(session, caller, "alert.acknowledged", alert)
    await bus.notify(session, bus.EVENTS, alert_event(alert))
    return alert


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut, responses={404: {}, 409: {}})
async def resolve(
    alert_id: uuid.UUID, body: AlertResolveIn, session: Session, caller: OwnerOrAdmin
):
    """Close an alert by hand, e.g. after fixing the problem. If the problem is
    still there, the next reading opens a new alert."""
    alert = await visible_alert(session, caller, alert_id)
    _may_change(caller, alert)
    if alert.state not in OPEN_ALERT_STATES:
        raise HTTPException(http.HTTP_409_CONFLICT, "This alert is already closed")
    alert.state = "resolved"
    alert.resolved_at, alert.resolved_by = datetime.now(UTC), caller.id
    await _log(session, caller, "alert.resolved", alert, note=body.note)
    await bus.notify(session, bus.EVENTS, alert_event(alert))
    return alert


# ----- the owner's rules (FR-62) -----


def _rules_out(building: Building) -> AlertRulesOut:
    settings = settings_for(building.alert_rules, building.alert_recipients)
    return AlertRulesOut(rules=describe(settings), recipients=settings.recipients)


@router.get(
    "/buildings/{building_id}/alert-rules", response_model=AlertRulesOut, responses={404: {}}
)
async def get_rules(building_id: uuid.UUID, session: Session, caller: OwnerOrAdmin):
    """Every built-in rule: on or off, its numbers and their safe limits, and
    who gets an email for each severity."""
    return _rules_out(await visible_building(session, caller, building_id))


@router.patch(
    "/buildings/{building_id}/alert-rules",
    response_model=AlertRulesOut,
    responses={404: {}, 422: {}},
)
async def change_rules(
    building_id: uuid.UUID, body: AlertRulesIn, session: Session, caller: OwnerOnly
):
    """Switch rules on or off and change their numbers, within safe limits.
    Only what you send changes (to go back to a default, send the default
    value). Audited. The worker uses the new settings within 10 seconds."""
    building = await visible_building(session, caller, building_id)
    rules = {r: dict(building.alert_rules.get(r, {})) | v for r, v in body.rules.items()}
    recipients = body.recipients or {}
    problems = check(rules, recipients)
    if problems:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(problems))
    before = {"rules": building.alert_rules, "recipients": building.alert_recipients}
    building.alert_rules = building.alert_rules | rules
    building.alert_recipients = building.alert_recipients | recipients
    await audit.record(
        session,
        "alert_rules.changed",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="building",
        target_id=building.id,
        building_id=building.id,
        details={"before": before, "changes": {"rules": body.rules, "recipients": recipients}},
    )
    return _rules_out(building)


# ----- my email choice (FR-60) -----


def _prefs_out(user) -> NotificationPrefsOut:
    out = NotificationPrefsOut.model_validate(user)
    out.push_available = push.available()
    return out


@router.get("/me/notifications", response_model=NotificationPrefsOut)
async def my_prefs(caller: AnyRole):
    return _prefs_out(caller.user)


@router.patch("/me/notifications", response_model=NotificationPrefsOut, responses={422: {}})
async def change_my_prefs(body: NotificationPrefsIn, caller: AnyRole):
    """Which alerts also come by email and by push (FR-52): this severity and
    above (or "off"), alert types never sent that way, and quiet hours when
    non-critical emails and pushes wait. In-app alerts are always on. Takes
    effect for the next alert."""
    sent = body.model_dump(exclude_unset=True)
    named = set(sent.get("email_muted_rules") or []) | set(sent.get("push_muted_rules") or [])
    unknown = named - set(RULES) - {AUDIT_RULE.id}
    if unknown:
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown alert type: {', '.join(sorted(unknown))}"
        )
    quiet = {k: sent[k] for k in ("quiet_from", "quiet_until") if k in sent}
    if quiet and (
        len(quiet) == 1 or (quiet["quiet_from"] is None) != (quiet["quiet_until"] is None)
    ):
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_CONTENT,
            "Set both quiet_from and quiet_until (or both to null for no quiet hours)",
        )
    if quiet.get("quiet_from") is not None and quiet["quiet_from"] == quiet["quiet_until"]:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Quiet hours need a length")
    for field, value in sent.items():
        if value is not None or field.startswith("quiet_"):
            setattr(caller.user, field, sorted(set(value)) if field.endswith("_rules") else value)
    return _prefs_out(caller.user)


# ----- my phones for push notifications (M9, FR-60) -----


@router.post("/me/push-devices", status_code=204, responses={422: {}})
async def add_push_device(body: PushDeviceIn, session: Session, caller: AnyRole):
    """The Android app sends its Firebase token after login (and when Firebase
    gives it a new one). A token belongs to one person: if someone else logs
    in on the same phone, it moves to them."""
    phone = await session.scalar(select(PushDevice).where(PushDevice.token == body.token))
    if phone is None:
        session.add(PushDevice(user_id=caller.id, token=body.token, platform=body.platform))
    else:
        phone.user_id = caller.id
        phone.last_seen_at = datetime.now(UTC)


@router.post("/me/push-devices/forget", status_code=204)
async def forget_push_device(body: PushForgetIn, session: Session, caller: AnyRole):
    """Logging out in the app: no more pushes to this phone."""
    await session.execute(
        delete(PushDevice).where(PushDevice.token == body.token, PushDevice.user_id == caller.id)
    )
