"""M10 part 1: AI leak detection (FR-63, FR-66).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31

* `alerts.details`: an AI alert's confidence, the numbers its explanation
  was made from, and a small evidence chart (FR-66).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('details', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('alerts', 'details')
