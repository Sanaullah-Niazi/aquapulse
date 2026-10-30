"""The audit log (FR-28): who did what, when, from where.

Entries are only ever added. The API's database role cannot UPDATE or
DELETE them (see the migration). Each entry also stores a SHA-256 hash of
its content plus the previous entry's hash, a "hash chain": changing or
deleting any old entry breaks every hash after it, which `verify_chain`
detects (FR-73).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditCheck, AuditEntry

GENESIS = b"\x00" * 32  # "previous hash" of the very first entry
_LOCK_ID = 7_465_621  # any fixed number: one writer at a time keeps the chain in order


def _digest(entry: Any) -> bytes:  # an AuditEntry, or a row with its columns
    content = {
        "at": entry.at.isoformat(),
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "actor_role": entry.actor_role,
        "ip": str(entry.ip) if entry.ip else None,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "building_id": str(entry.building_id) if entry.building_id else None,
        "details": entry.details,
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(entry.prev_hash + canonical.encode()).digest()


async def record(
    session: AsyncSession,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    ip: str | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    building_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEntry:
    """Add one entry. It is saved together with the rest of the request's changes."""
    # Lock until this transaction ends, so two requests cannot both pick the
    # same "previous" entry and fork the chain.
    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _LOCK_ID})
    prev = await session.scalar(select(AuditEntry.hash).order_by(AuditEntry.id.desc()).limit(1))
    entry = AuditEntry(
        at=datetime.now(UTC),
        actor_id=actor_id,
        actor_role=actor_role,
        ip=ip,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        building_id=building_id,
        details=details or {},
        prev_hash=prev or GENESIS,
    )
    entry.hash = _digest(entry)
    session.add(entry)
    await session.flush()
    return entry


async def verify_chain(session: AsyncSession) -> tuple[bool, int | None]:
    """Check every entry. Returns (ok, id of the first broken entry)."""
    ok, broken, _ = await _walk(session)
    return ok, broken


async def _walk(session: AsyncSession) -> tuple[bool, int | None, int]:
    prev, count = GENESIS, 0
    # Plain rows instead of ORM objects: about 3 times faster (FR-73 criterion 2).
    columns = [getattr(AuditEntry, c) for c in _COLUMNS]
    result = await session.stream(select(*columns).order_by(AuditEntry.id).execution_options(
        yield_per=5000))  # fmt: skip
    async for entry in result:
        count += 1
        if entry.prev_hash != prev or _digest(entry) != entry.hash:
            return False, entry.id, count
        prev = entry.hash
    return True, None, count


_COLUMNS = ("id", "at", "actor_id", "actor_role", "ip", "action", "target_type", "target_id",
            "building_id", "details", "prev_hash", "hash")  # fmt: skip


async def check_chain(
    session: AsyncSession, requested_by: uuid.UUID | None = None, at: datetime | None = None
) -> AuditCheck:
    """Verify the whole chain and keep the result (daily by the worker, or on
    demand by a System Admin). The caller commits."""
    started = time.perf_counter()
    ok, broken, count = await _walk(session)
    check = AuditCheck(
        at=at or datetime.now(UTC),
        ok=ok,
        entries=count,
        first_broken_id=broken,
        seconds=round(time.perf_counter() - started, 3),
        requested_by=requested_by,
    )
    session.add(check)
    await session.flush()
    return check
