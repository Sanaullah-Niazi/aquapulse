"""M8 part 3: service heartbeats for the System Admin health page (FR-55, FR-31).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31

* `service_heartbeats`: one row per background service (ingestor, worker).
  Each service writes "I am alive" with a few numbers every few seconds, so
  the API can show red or green for services it cannot see directly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('service_heartbeats',
        sa.Column('service', sa.String(length=32), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('details', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.PrimaryKeyConstraint('service'),
    )


def downgrade() -> None:
    op.drop_table('service_heartbeats')
