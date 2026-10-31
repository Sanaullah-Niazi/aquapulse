"""M12: keep raw readings for 13 months only (FR-75 AC3, NFR-14).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31

TimescaleDB drops whole one-day chunks of `telemetry` and `tank_readings`
once they are older than 395 days (13 months). The 15-minute totals in
`consumption_15m` stay: its refresh only looks at the last 3 days, so old
totals are never recalculated from the deleted readings. Bills and charts
keep working for older months.
"""

from __future__ import annotations

from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

TABLES = ("telemetry", "tank_readings")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"SELECT add_retention_policy('{table}', drop_after => INTERVAL '395 days',"
            " if_not_exists => true)"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => true)")
