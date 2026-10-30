"""From signals to alerts in the database, and who is told (FR-59, FR-60).

* A new problem opens an alert, tells the live stream (in-app, always) and
  queues an email for each person who should get one.
* The same problem again only updates that alert (de-duplication).
* A problem that is gone sets `cleared_at`; ten minutes later, if it did not
  come back, `auto_resolve` closes the alert.

The slower rules live here too, because they need the database: a device that
stopped sending (`offline_signals`) and a day with high use (`usage_signals`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events as bus
from ..billing.periods import local_midnight
from ..config import get_settings
from ..models import (
    OPEN_ALERT_STATES,
    Alert,
    AuditCheck,
    Building,
    Device,
    Home,
    Notification,
    Occupancy,
    Tank,
    User,
)
from .detect import Signal
from .rules import AUDIT_RULE, RULES, SEVERITY_ORDER, RuleSettings, settings_for

log = logging.getLogger("aquapulse.alerts")

CLEAR_FOR = timedelta(minutes=10)  # FR-59 criterion 2
MIN_HISTORY_DAYS = 7  # high use: compare only with at least a week of history
MIN_AVERAGE_L = 20.0  # ... and only homes that really use water


def _uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def alert_event(alert: Alert) -> dict[str, Any]:
    """What the live stream shows (FR-25 rules decide who sees it)."""
    return {
        "type": "alert",
        "alertId": alert.id,
        "buildingId": alert.building_id,
        "homeId": alert.home_id,
        "tankId": alert.tank_id,
        "deviceId": alert.device_id,
        "rule": alert.rule,
        "severity": alert.severity,
        "state": alert.state,
        "title": alert.title,
        "message": alert.message,
        "openedAt": alert.opened_at.isoformat() if alert.opened_at else None,
    }


async def load_settings(session: AsyncSession) -> tuple[dict[str, RuleSettings], dict[str, float]]:
    """Every building's rule settings, and every tank's high threshold."""
    rows = await session.execute(
        select(Building.id, Building.alert_rules, Building.alert_recipients)
    )
    settings = {str(r.id): settings_for(r.alert_rules, r.alert_recipients) for r in rows}
    tanks = {str(t.id): float(t.high_pct) for t in await session.scalars(select(Tank))}
    return settings, tanks


async def open_keys(session: AsyncSession) -> set[str]:
    """Problems that are still on (after a restart, so they can be cleared)."""
    rows = await session.scalars(
        select(Alert.dedupe_key).where(
            Alert.state.in_(OPEN_ALERT_STATES), Alert.cleared_at.is_(None)
        )
    )
    return set(rows)


# ----- signals -> alerts -----


async def apply(session: AsyncSession, signals: list[Signal], now: datetime) -> list[Alert]:
    """Save signals. Returns the alerts that were newly opened."""
    opened = []
    for signal in signals:
        current = await session.scalar(
            select(Alert)
            .where(Alert.dedupe_key == signal.key, Alert.state.in_(OPEN_ALERT_STATES))
            .with_for_update()
        )
        if not signal.active:
            if current is not None and current.cleared_at is None:
                current.cleared_at = now
            continue
        if current is not None:
            current.last_seen_at = now
            current.cleared_at = None
            current.seen_count += 1
            current.value = signal.value
            if signal.message:
                current.message = signal.message
            continue
        alert = await _open(session, signal, now)
        if alert is not None:
            opened.append(alert)
    return opened


async def _open(session: AsyncSession, signal: Signal, now: datetime) -> Alert | None:
    rule = AUDIT_RULE if signal.rule == AUDIT_RULE.id else RULES[signal.rule]
    building_id = _uuid(signal.building_id)
    if building_id is not None and await session.get(Building, building_id) is None:
        return None  # e.g. an event from a test that has gone
    alert = Alert(
        rule=rule.id,
        severity=rule.severity,
        dedupe_key=signal.key,
        building_id=building_id,
        home_id=_uuid(signal.home_id),
        tank_id=_uuid(signal.tank_id),
        device_id=signal.device_id,
        title=rule.title,
        message=signal.message or rule.describe,
        value=signal.value,
        opened_at=now,
        last_seen_at=now,
    )
    session.add(alert)
    await session.flush()
    await bus.notify(session, bus.EVENTS, alert_event(alert))
    await queue_emails(session, alert, now)
    log.info("alert opened: %s %s", alert.severity, alert.dedupe_key)
    return alert


async def auto_resolve(session: AsyncSession, now: datetime) -> int:
    """Close alerts whose problem has been gone for 10 minutes (FR-59 criterion 2)."""
    closed = list(
        await session.scalars(
            update(Alert)
            .where(
                Alert.state.in_(OPEN_ALERT_STATES),
                Alert.cleared_at.is_not(None),
                Alert.cleared_at <= now - CLEAR_FOR,
            )
            .values(state="auto_resolved", resolved_at=now)
            .returning(Alert)
            .execution_options(populate_existing=True)
        )
    )
    for alert in closed:
        await bus.notify(session, bus.EVENTS, alert_event(alert))
    return len(closed)


# ----- who is told -----


async def recipients(session: AsyncSession, alert: Alert) -> list[User]:
    """People who get an email: the building's choice per severity (FR-62),
    then each person's own minimum severity (FR-60)."""
    if alert.building_id is None:  # a system alert: the System Admins
        users = list(
            await session.scalars(select(User).where(User.role == "sysadmin", User.is_active))
        )
    else:
        building = await session.get(Building, alert.building_id)
        assert building is not None
        kinds = settings_for(building.alert_rules, building.alert_recipients).recipients[
            alert.severity
        ]
        ids: set[uuid.UUID] = set()
        if "owner" in kinds:
            ids.add(building.owner_id)
        if "resident" in kinds and alert.home_id is not None:
            today = datetime.now(UTC).astimezone(ZoneInfo(building.timezone)).date()
            ids |= set(
                await session.scalars(
                    select(Occupancy.user_id).where(
                        Occupancy.home_id == alert.home_id,
                        Occupancy.move_in <= today,
                        or_(Occupancy.move_out.is_(None), Occupancy.move_out >= today),
                    )
                )
            )
        users = list(await session.scalars(select(User).where(User.id.in_(ids), User.is_active)))
    wanted = SEVERITY_ORDER[alert.severity]
    return [
        u
        for u in users
        if u.alert_email_min != "off" and SEVERITY_ORDER[u.alert_email_min] <= wanted
    ]


async def _where(session: AsyncSession, alert: Alert) -> str:
    if alert.building_id is None:
        return "AquaPulse server"
    building = await session.get(Building, alert.building_id)
    assert building is not None
    if alert.home_id is not None:
        home = await session.get(Home, alert.home_id)
        return f"unit {home.unit_no if home else '?'}, {building.name}"
    if alert.tank_id is not None:
        return f"water tank, {building.name}"
    return f"device {alert.device_id}, {building.name}"


async def queue_emails(session: AsyncSession, alert: Alert, now: datetime) -> int:
    people = await recipients(session, alert)
    if not people:
        return 0
    where = await _where(session, alert)
    subject = f"[AquaPulse] {alert.severity.capitalize()}: {alert.title} ({where})"
    body = (
        f"{alert.title} - {where}\n\n{alert.message}\n\n"
        f"Severity: {alert.severity}\nStarted: {alert.opened_at:%Y-%m-%d %H:%M} UTC\n\n"
        f"Open AquaPulse to see and acknowledge it: {get_settings().public_url}\n\n"
        "You get this email because of your alert settings "
        "(PATCH /api/v1/me/notifications). Alerts are always shown in the app.\n"
    )
    for person in people:
        session.add(
            Notification(
                user_id=person.id,
                channel="email",
                kind="alert",
                alert_id=alert.id,
                subject=subject,
                body=body,
                next_attempt_at=now,
            )
        )
    return len(people)


# ----- the slower rules -----


async def offline_signals(
    session: AsyncSession, settings: dict[str, RuleSettings], now: datetime
) -> list[Signal]:
    """Devices that were sending but have been quiet for too long (FR-58).
    Devices that never sent anything (just registered) are not counted."""
    rows = await session.execute(
        select(
            Device.id, Device.building_id, Device.home_id, Device.tank_id, Device.last_seen_at
        ).where(Device.status == "active", Device.last_seen_at.is_not(None))
    )
    signals = []
    for r in rows:
        s = settings.get(str(r.building_id))
        if s is None or not s.on("device_offline"):
            continue
        quiet = (now - r.last_seen_at).total_seconds()
        if quiet >= s.get("device_offline", "seconds"):
            signals.append(
                Signal(
                    "device_offline",
                    f"device_offline:{r.id}",
                    True,
                    str(r.building_id),
                    str(r.home_id) if r.home_id else None,
                    str(r.tank_id) if r.tank_id else None,
                    r.id,
                    quiet,
                    f"No message for {quiet / 60:.0f} minutes",
                )
            )
    return signals


USAGE_SQL = text(
    "SELECT home_id, "
    "  COALESCE(SUM(litres) FILTER (WHERE bucket >= :today), 0) AS today_l, "
    "  COALESCE(SUM(litres) FILTER (WHERE bucket < :today), 0) AS history_l, "
    "  MIN(bucket) FILTER (WHERE bucket < :today) AS first_bucket "
    "FROM consumption_15m WHERE building_id = :building "
    "  AND bucket >= :history_start AND bucket < :end GROUP BY home_id"
)


def _usage_text(today_l: float, average: float) -> str:
    share = today_l / average * 100
    return f"{today_l:,.0f} L today, {share:.0f} % of an average day ({average:,.0f} L)"


async def usage_signals(
    session: AsyncSession, building: Building, settings: RuleSettings, now: datetime
) -> list[Signal]:
    """Daily use high (FR-58): after 18:00 building time, today's litres
    against the home's average day over the 30 days before today."""
    if not settings.on("high_usage"):
        return []
    zone = ZoneInfo(building.timezone)
    local = now.astimezone(zone)
    today: date = local.date()
    today_start = local_midnight(today, building.timezone)
    rows = await session.execute(
        USAGE_SQL,
        {
            "building": building.id,
            "today": today_start,
            "history_start": local_midnight(today - timedelta(days=30), building.timezone),
            "end": now,
        },
    )
    percent = settings.get("high_usage", "percent")
    late_enough = local.hour >= settings.get("high_usage", "after_hour")
    signals = []
    for r in rows:
        key = f"high_usage:{r.home_id}"
        # Days of history, counted in local dates (the first day counts whole).
        days = (
            0 if r.first_bucket is None else (today - r.first_bucket.astimezone(zone).date()).days
        )
        average = float(r.history_l) / days if days else 0.0
        high = (
            late_enough
            and days >= MIN_HISTORY_DAYS
            and average >= MIN_AVERAGE_L
            and float(r.today_l) > average * percent / 100
        )
        signals.append(
            Signal(
                "high_usage",
                key,
                high,
                str(building.id),
                str(r.home_id),
                value=round(float(r.today_l), 1),
                message=_usage_text(float(r.today_l), average) if high else "",
            )
        )
    return signals


async def clear_disabled(session: AsyncSession, settings: dict[str, RuleSettings], now: datetime):
    """When the owner switches a rule off, its open alerts count as cleared."""
    for building_id, s in settings.items():
        off = [rule for rule in RULES if not s.on(rule)]
        if off:
            await session.execute(
                update(Alert)
                .where(
                    Alert.building_id == uuid.UUID(building_id),
                    Alert.rule.in_(off),
                    Alert.state.in_(OPEN_ALERT_STATES),
                    Alert.cleared_at.is_(None),
                )
                .values(cleared_at=now)
            )


async def audit_signal_saved(session: AsyncSession, check: AuditCheck, now: datetime) -> None:
    """A broken audit chain opens a critical alert for System Admins (FR-73);
    a good check clears it."""
    message = (
        f"Audit entry {check.first_broken_id} was changed or removed: the chain is broken there"
        if not check.ok
        else ""
    )
    await apply(session, [Signal(AUDIT_RULE.id, AUDIT_RULE.id, not check.ok, message=message)], now)
