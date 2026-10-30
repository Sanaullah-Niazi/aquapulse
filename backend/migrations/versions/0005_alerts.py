"""M7: alerts, notifications, bill reminders and audit checks (FR-38, FR-58 to FR-62, FR-73).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30

* `alerts`: one row per problem; a partial unique index on the dedupe key
  makes sure there is only one open alert per problem (FR-59).
* `notifications`: every email to send, with its attempts (FR-60).
* `bill_reminders`: which reminder was sent for which bill, so each is sent once (FR-38).
* `audit_checks`: the result of each audit chain check (FR-73).
* `buildings.alert_rules` / `alert_recipients`: the owner's rule settings (FR-62).
* `users.alert_email_min`: from which severity a person also gets an email.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column('id', sa.UUID(), nullable=False)


def _when(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'alert_email_min', sa.String(length=8), server_default='warning', nullable=False))
    op.add_column('buildings', sa.Column(
        'alert_rules', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('buildings', sa.Column(
        'alert_recipients', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"),
        nullable=False))

    op.create_table('alerts',
        _id(),
        sa.Column('rule', sa.String(length=24), nullable=False),
        sa.Column('severity', sa.String(length=8), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('dedupe_key', sa.String(length=80), nullable=False),
        sa.Column('building_id', sa.UUID(), nullable=True),
        sa.Column('home_id', sa.UUID(), nullable=True),
        sa.Column('tank_id', sa.UUID(), nullable=True),
        sa.Column('device_id', sa.String(length=32), nullable=True),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('seen_count', sa.Integer(), nullable=False),
        _when('opened_at', False),
        _when('last_seen_at', False),
        _when('cleared_at'),
        _when('acknowledged_at'),
        sa.Column('acknowledged_by', sa.UUID(), nullable=True),
        _when('resolved_at'),
        sa.Column('resolved_by', sa.UUID(), nullable=True),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name='severity_valid'),
        sa.CheckConstraint(
            "state IN ('open', 'acknowledged', 'resolved', 'auto_resolved')", name='state_valid'
        ),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id']),
        sa.ForeignKeyConstraint(['home_id'], ['homes.id']),
        sa.ForeignKeyConstraint(['tank_id'], ['tanks.id']),
        sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id']),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_alerts_home_id', 'alerts', ['home_id'])
    op.create_index('alerts_building_opened', 'alerts', ['building_id', 'opened_at'])
    op.create_index(
        'one_open_alert_per_problem', 'alerts', ['dedupe_key'], unique=True,
        postgresql_where=sa.text("state IN ('open', 'acknowledged')"),
    )  # fmt: skip

    op.create_table('notifications',
        _id(),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.String(length=8), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('alert_id', sa.UUID(), nullable=True),
        sa.Column('bill_id', sa.UUID(), nullable=True),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=8), nullable=False),
        sa.Column('attempts', sa.SmallInteger(), nullable=False),
        _when('next_attempt_at', False),
        sa.Column('last_error', sa.String(length=200), nullable=True),
        _when('sent_at'),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name='status_valid'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['alert_id'], ['alerts.id']),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])
    op.create_index('ix_notifications_alert_id', 'notifications', ['alert_id'])
    op.create_index(
        'notifications_due', 'notifications', ['next_attempt_at'],
        postgresql_where=sa.text("status = 'pending'"),
    )  # fmt: skip

    op.create_table('bill_reminders',
        sa.Column('bill_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column(
            'sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('before_due', 'due_today', 'after_due')", name='kind_valid'
        ),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.PrimaryKeyConstraint('bill_id', 'kind'),
    )  # fmt: skip

    op.create_table('audit_checks',
        _id(),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('entries', sa.BigInteger(), nullable=False),
        sa.Column('first_broken_id', sa.BigInteger(), nullable=True),
        sa.Column('seconds', sa.Float(), nullable=False),
        sa.Column('requested_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip


def downgrade() -> None:
    op.drop_table('audit_checks')
    op.drop_table('bill_reminders')
    op.drop_table('notifications')
    op.drop_table('alerts')
    op.drop_column('buildings', 'alert_recipients')
    op.drop_column('buildings', 'alert_rules')
    op.drop_column('users', 'alert_email_min')
