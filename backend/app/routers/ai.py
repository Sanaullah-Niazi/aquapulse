"""AI insights for a home (FR-63, FR-66). Forecasts follow in M10 part 2."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts.rules import AI_RULES
from ..db import get_session
from ..deps import Caller, require, visible_home
from ..models import Alert
from ..schemas import InsightOut
from .alerts import _resident_can_see

router = APIRouter(tags=["ai"])
Session = Annotated[AsyncSession, Depends(get_session)]
AnyRole = Annotated[Caller, Depends(require("owner", "sysadmin", "resident"))]


def insight(alert: Alert) -> InsightOut:
    details = dict(alert.details or {})
    confidence = details.pop("confidence", None)
    high = bool(details.pop("high", False))
    return InsightOut(
        alert_id=alert.id,
        kind=alert.rule,
        title=alert.title,
        severity=alert.severity,
        state=alert.state,
        explanation=alert.message,
        confidence=confidence,
        high_confidence=high,
        opened_at=alert.opened_at,
        last_seen_at=alert.last_seen_at,
        resolved_at=alert.resolved_at,
        evidence=details,
    )


@router.get("/homes/{home_id}/insights", response_model=list[InsightOut], responses={404: {}})
async def home_insights(
    home_id: uuid.UUID,
    session: Session,
    caller: AnyRole,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    """Leak and burst findings of the last `days` days, newest first, each with
    a plain-language reason and the readings behind it."""
    home = await visible_home(session, caller, home_id)
    query = (
        select(Alert)
        .where(
            Alert.home_id == home.id,
            Alert.rule.in_(AI_RULES),
            Alert.opened_at >= datetime.now(UTC) - timedelta(days=days),
        )
        .order_by(Alert.opened_at.desc())
        .limit(100)
    )
    if caller.role == "resident":
        query = query.where(_resident_can_see(caller.id))
    return [insight(a) for a in await session.scalars(query)]
