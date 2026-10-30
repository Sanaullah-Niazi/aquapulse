"""M6: tariffs, bills and payments (FR-32 to FR-40).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30

* `buildings.code` (B01, B02 ...) for bill numbers, given by the database
  from a sequence, and `buildings.bills_auto_issue`.
* Tariff versions with their slabs and charges.
* Bills, their lines, a bill-number counter per building and year, and a
  record of each monthly generation run.
* Payments and Stripe Checkout sessions.

All money is NUMERIC(14,3): exact decimals, enough for 3-decimal currencies.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 3)


def _id() -> sa.Column:
    return sa.Column('id', sa.UUID(), nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
    )


def upgrade() -> None:
    # --- buildings: a code for bill numbers, and auto-issue ---
    op.execute("CREATE SEQUENCE building_code_seq")
    # B01 ... B99, then B100: never shorter than 2 digits, never cut off.
    op.execute(
        "CREATE FUNCTION next_building_code() RETURNS text LANGUAGE sql AS $$"
        " SELECT 'B' || lpad(n::text, greatest(2, length(n::text)), '0')"
        " FROM (SELECT nextval('building_code_seq') AS n) AS s $$"
    )
    op.add_column('buildings', sa.Column('code', sa.String(length=8), nullable=True))
    # Existing buildings get B01, B02 ... in the order they were created.
    op.execute(
        "UPDATE buildings SET code = next_building_code() FROM ("
        " SELECT id FROM buildings ORDER BY created_at, id) AS ordered"
        " WHERE buildings.id = ordered.id"
    )
    op.alter_column(
        'buildings', 'code', nullable=False, server_default=sa.text('next_building_code()')
    )
    op.create_unique_constraint('buildings_code_key', 'buildings', ['code'])
    op.add_column(
        'buildings',
        sa.Column('bills_auto_issue', sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    # --- tariffs ---
    op.create_table('tariffs',
        _id(),
        sa.Column('building_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id']),
        sa.ForeignKeyConstraint(['currency_code'], ['currencies.code']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('building_id', 'version', name='tariff_version_unique'),
    )  # fmt: skip
    op.create_table('tariff_slabs',
        _id(),
        sa.Column('tariff_id', sa.UUID(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('from_l', sa.Integer(), nullable=False),
        sa.Column('to_l', sa.Integer(), nullable=True),
        sa.Column('rate_per_kl', MONEY, nullable=False),
        sa.CheckConstraint('rate_per_kl >= 0', name='rate_not_negative'),
        sa.ForeignKeyConstraint(['tariff_id'], ['tariffs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_tariff_slabs_tariff_id', 'tariff_slabs', ['tariff_id'])
    op.create_table('tariff_charges',
        _id(),
        sa.Column('tariff_id', sa.UUID(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('value', MONEY, nullable=False),
        sa.CheckConstraint(
            "kind IN ('tax_percent', 'extra_percent', 'extra_fixed')", name='kind_valid'
        ),
        sa.CheckConstraint('value >= 0', name='value_not_negative'),
        sa.ForeignKeyConstraint(['tariff_id'], ['tariffs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_tariff_charges_tariff_id', 'tariff_charges', ['tariff_id'])

    # --- bills ---
    op.create_table('bills',
        _id(),
        sa.Column('bill_no', sa.String(length=24), nullable=True),
        sa.Column('building_id', sa.UUID(), nullable=False),
        sa.Column('home_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('bill_to', sa.String(length=120), nullable=False),
        sa.Column('unit_no', sa.String(length=20), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('month_days', sa.SmallInteger(), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('litres', MONEY, nullable=False),
        sa.Column('reading_start', MONEY, nullable=True),
        sa.Column('reading_end', MONEY, nullable=True),
        sa.Column('subtotal', MONEY, nullable=False),
        sa.Column('tax_total', MONEY, nullable=False),
        sa.Column('total', MONEY, nullable=False),
        sa.Column('amount_paid', MONEY, nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('issued_by', sa.UUID(), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_by', sa.UUID(), nullable=True),
        sa.Column('void_reason', sa.String(length=300), nullable=True),
        sa.Column('replaces_bill_id', sa.UUID(), nullable=True),
        sa.Column('shutoff_warned_at', sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'issued', 'partially_paid', 'paid', 'overdue', 'void')",
            name='status_valid',
        ),
        sa.CheckConstraint('period_end >= period_start', name='period_in_order'),
        sa.CheckConstraint('total >= 0 AND amount_paid >= 0', name='amounts_not_negative'),
        sa.CheckConstraint(
            "status = 'draft' OR status = 'void' OR (bill_no IS NOT NULL AND due_date IS NOT NULL)",
            name='issued_bills_have_number_and_due_date',
        ),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id']),
        sa.ForeignKeyConstraint(['home_id'], ['homes.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['currency_code'], ['currencies.code']),
        sa.ForeignKeyConstraint(['issued_by'], ['users.id']),
        sa.ForeignKeyConstraint(['voided_by'], ['users.id']),
        sa.ForeignKeyConstraint(['replaces_bill_id'], ['bills.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bill_no'),
    )  # fmt: skip
    op.create_index('ix_bills_home_id', 'bills', ['home_id'])
    op.create_index('ix_bills_user_id', 'bills', ['user_id'])
    op.create_index('bills_building_month', 'bills', ['building_id', 'month'])
    op.create_index(
        'one_bill_per_period', 'bills', ['home_id', 'user_id', 'period_start'], unique=True,
        postgresql_where=sa.text("status <> 'void'"),
    )
    op.create_table('bill_lines',
        _id(),
        sa.Column('bill_id', sa.UUID(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('description', sa.String(length=160), nullable=False),
        sa.Column('quantity_l', MONEY, nullable=True),
        sa.Column('rate', MONEY, nullable=True),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('tariff_id', sa.UUID(), nullable=True),
        sa.CheckConstraint("kind IN ('slab', 'extra', 'tax')", name='kind_valid'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tariff_id'], ['tariffs.id']),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_bill_lines_bill_id', 'bill_lines', ['bill_id'])
    op.create_table('bill_counters',
        sa.Column('building_id', sa.UUID(), nullable=False),
        sa.Column('year', sa.SmallInteger(), nullable=False),
        sa.Column('last_no', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id']),
        sa.PrimaryKeyConstraint('building_id', 'year'),
    )  # fmt: skip
    op.create_table('billing_runs',
        sa.Column('building_id', sa.UUID(), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('automatic', sa.Boolean(), nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id']),
        sa.PrimaryKeyConstraint('building_id', 'month'),
    )  # fmt: skip

    # --- payments ---
    op.create_table('payments',
        _id(),
        sa.Column('bill_id', sa.UUID(), nullable=False),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('method', sa.String(length=12), nullable=False),
        sa.Column('reference', sa.String(length=100), nullable=False),
        sa.Column('provider_ref', sa.String(length=255), nullable=True),
        sa.Column('received_on', sa.Date(), nullable=False),
        sa.Column('recorded_by', sa.UUID(), nullable=True),
        sa.Column('receipt', sa.LargeBinary(), nullable=True),
        sa.Column('receipt_type', sa.String(length=16), nullable=True),
        sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reversed_by', sa.UUID(), nullable=True),
        sa.Column('reversal_reason', sa.String(length=300), nullable=True),
        _created(),
        sa.CheckConstraint(
            "method IN ('cash', 'bank', 'stripe_test', 'other')", name='method_valid'
        ),
        sa.CheckConstraint('amount > 0', name='amount_positive'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id']),
        sa.ForeignKeyConstraint(['reversed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_ref'),
    )  # fmt: skip
    op.create_index('ix_payments_bill_id', 'payments', ['bill_id'])
    op.create_table('checkout_sessions',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('bill_id', sa.UUID(), nullable=False),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=False),
        _created(),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )  # fmt: skip
    op.create_index('ix_checkout_sessions_bill_id', 'checkout_sessions', ['bill_id'])


def downgrade() -> None:
    op.drop_table('checkout_sessions')
    op.drop_table('payments')
    op.drop_table('billing_runs')
    op.drop_table('bill_counters')
    op.drop_table('bill_lines')
    op.drop_table('bills')
    op.drop_table('tariff_charges')
    op.drop_table('tariff_slabs')
    op.drop_table('tariffs')
    op.drop_column('buildings', 'bills_auto_issue')
    op.drop_constraint('buildings_code_key', 'buildings', type_='unique')
    op.drop_column('buildings', 'code')
    op.execute("DROP FUNCTION next_building_code()")
    op.execute("DROP SEQUENCE building_code_seq")
