"""M9 part 3: push notifications to the Android app (FR-60, FR-52).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31

* `push_devices`: each phone's Firebase Cloud Messaging token.
* `users.push_min` / `users.push_muted_rules`: each person's push choice,
  like the email choice from M7 and M8.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('push_min', sa.String(length=8), server_default='warning',
                                     nullable=False))
    op.add_column('users', sa.Column('push_muted_rules', postgresql.JSONB(),
                                     server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.create_table('push_devices',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token', sa.String(length=4096), nullable=False),
        sa.Column('platform', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index(op.f('ix_push_devices_user_id'), 'push_devices', ['user_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_push_devices_user_id'), table_name='push_devices')
    op.drop_table('push_devices')
    op.drop_column('users', 'push_muted_rules')
    op.drop_column('users', 'push_min')
