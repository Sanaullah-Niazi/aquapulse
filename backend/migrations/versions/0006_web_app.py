"""M8: two-factor login and more notification choices (FR-70, FR-52).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30

* `users.totp_*`: the encrypted authenticator secret, when 2FA was turned on,
  and the last 30-second step used (so a code works once).
* `recovery_codes`: one-time codes for a lost phone (only keyed hashes).
* `users.email_muted_rules`, `quiet_from`, `quiet_until`: alert types never
  sent by email, and quiet hours for non-critical emails.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'email_muted_rules', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"),
        nullable=False))
    op.add_column('users', sa.Column('quiet_from', sa.Time(), nullable=True))
    op.add_column('users', sa.Column('quiet_until', sa.Time(), nullable=True))
    op.add_column('users', sa.Column('totp_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))

    op.create_table('recovery_codes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('code_hash', sa.LargeBinary(length=32), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_recovery_codes_user_id', 'recovery_codes', ['user_id'])


def downgrade() -> None:
    op.drop_table('recovery_codes')
    for column in ('totp_last_step', 'totp_enabled_at', 'totp_secret', 'quiet_until',
                   'quiet_from', 'email_muted_rules'):  # fmt: skip
        op.drop_column('users', column)
