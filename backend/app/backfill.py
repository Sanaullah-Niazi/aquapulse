"""Load demo history made by the simulator (FR-08): `make demo-data`.

    aquapulse-sim backfill --days 90  |  python -m app.backfill

Every line goes through the same Handler as live MQTT messages (contract
check, registered device, de-duplication, litres from the counter); only
the "within 10 minutes of now" clock check is skipped, because history is
old by definition.

History is only added *before* the first live reading of each device:
* running it twice adds nothing (everything is already older than the
  first stored reading, or already stored);
* history never mixes with live data, so live litres are never compared
  with a simulated counter.
That is why a device needs at least one live reading first (`make up`
starts the simulator and the ingestor; wait half a minute).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from . import contract, db
from .config import get_settings
from .ingest.handler import Handler, Incoming
from .models import DeviceEvent, TankReading, Telemetry

BATCH = 2000


@dataclass
class Summary:
    stored: int = 0
    duplicates: int = 0
    skipped: int = 0  # at or after the device's first live reading
    no_live_data: set[str] = field(default_factory=set)
    rejected: dict[str, int] = field(default_factory=dict)
    labels: int = 0
    first: datetime | None = None
    last: datetime | None = None


async def first_live_readings() -> dict[str, datetime]:
    """deviceId -> time of its oldest stored reading."""
    async with db.sessions()() as session:
        result: dict[str, datetime] = {}
        for model in (Telemetry, TankReading):
            rows = await session.execute(
                select(model.device_id, func.min(model.time)).group_by(model.device_id)
            )
            result.update({device_id: first for device_id, first in rows})
        return result


async def load(lines: Iterable[str]) -> Summary:
    summary = Summary()
    cutoff = await first_live_readings()
    handler = Handler(live=False)
    batch: list[Incoming] = []
    labels: list[dict] = []
    received = datetime.now(UTC)

    async def flush() -> None:
        result = await handler.handle(batch)
        summary.stored += result.stored
        summary.duplicates += result.duplicates
        for reason, count in result.rejected.items():
            summary.rejected[reason] = summary.rejected.get(reason, 0) + count
        batch.clear()

    for line in lines:
        if not line.strip():
            continue
        item = json.loads(line)
        if "label" in item:
            labels.append(item["label"])
            continue
        device_id = contract.parse_topic(item["topic"])[0]
        when = contract.parse_ts(item["payload"]["ts"])
        if device_id not in cutoff:
            summary.no_live_data.add(device_id)
            continue
        if when >= cutoff[device_id]:
            summary.skipped += 1
            continue
        summary.first = min(summary.first or when, when)
        summary.last = max(summary.last or when, when)
        payload = json.dumps(item["payload"], separators=(",", ":")).encode()
        batch.append(Incoming(item["topic"], payload, received))
        if len(batch) >= BATCH:
            await flush()
    if batch:
        await flush()

    summary.labels = await _store_labels(labels, cutoff)
    if summary.first is not None and summary.last is not None:
        await refresh_consumption(summary.first, summary.last)
    return summary


async def _store_labels(labels: list[dict], cutoff: dict[str, datetime]) -> int:
    """The planted problems (leaks, pH drifts ...), kept as ground truth for M10."""
    rows = [
        {
            "device_id": label["deviceId"],
            "kind": label["kind"],
            "started_at": contract.parse_ts(label["start"]),
            "ended_at": contract.parse_ts(label["end"]),
            "source": "backfill",
            "details": label["details"],
        }
        for label in labels
        if label["deviceId"] in cutoff
        and contract.parse_ts(label["start"]) < cutoff[label["deviceId"]]
    ]
    if not rows:
        return 0
    async with db.sessions()() as session, session.begin():
        result = await session.execute(insert(DeviceEvent).values(rows).on_conflict_do_nothing())
        return result.rowcount or 0


async def refresh_consumption(first: datetime, last: datetime) -> None:
    """Add the new history to the 15-minute totals (consumption_15m).

    The automatic refresh only looks at the last 3 days, so old data that
    arrives late (like this history) must be added by hand. This needs the
    database owner and must run outside a transaction."""
    settings = get_settings()
    engine = create_async_engine(
        settings.db_url(owner=True), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "CALL refresh_continuous_aggregate('consumption_15m', "
                    "CAST(:start AS timestamptz), CAST(:end AS timestamptz))"
                ),
                {
                    "start": first.replace(minute=0, second=0, microsecond=0),
                    "end": last + timedelta(minutes=15),  # include the last 15-minute bucket
                },
            )
    finally:
        await engine.dispose()


def main() -> None:
    summary = asyncio.run(_main())
    days = (summary.last - summary.first).days + 1 if summary.first and summary.last else 0
    print(
        f"Demo history: {summary.stored} readings stored over {days} days, "
        f"{summary.labels} labelled events, {summary.duplicates} already there, "
        f"{summary.skipped} skipped (already loaded, or newer than the live data)."
    )
    if summary.rejected:
        print(f"Rejected: {summary.rejected}")
    if summary.no_live_data:
        print(
            "No live data yet for: " + ", ".join(sorted(summary.no_live_data)) + ". "
            "Their history was not loaded: wait until `make smoke` passes, then run again."
        )


async def _main() -> Summary:
    try:
        return await load(sys.stdin)
    finally:
        await db.dispose()


if __name__ == "__main__":
    main()
