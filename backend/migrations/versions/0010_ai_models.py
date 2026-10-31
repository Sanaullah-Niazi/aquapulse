"""M10 part 2: the AI model registry (FR-67).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31

* `ai_models`: every trained model version per building (unusual use,
  forecast), with its data range, scores and whether it is live.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ai_models',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('building_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('trained_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('data_from', sa.Date(), nullable=False),
        sa.Column('data_to', sa.Date(), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('metrics', postgresql.JSONB(), nullable=False),
        sa.Column('params', postgresql.JSONB(), nullable=False),
        sa.Column('live', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=False),
        sa.Column('blob', sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('building_id', 'kind', 'version', name='ai_model_version_unique'),
    )  # fmt: skip
    op.create_index('ix_ai_models_building_id', 'ai_models', ['building_id'])
    op.create_index('one_live_model', 'ai_models', ['building_id', 'kind'], unique=True,
                    postgresql_where=sa.text('live'))


def downgrade() -> None:
    op.drop_table('ai_models')
