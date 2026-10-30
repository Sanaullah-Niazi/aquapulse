"""M5: device credentials (FR-11, FR-14).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30

* `mqtt_password_hash`: the device's MQTT password, hashed the way Mosquitto
  expects. Never the plain password (FR-14 criterion 2). Empty for simulated
  devices that still use their simulator password.
* `credentials_changed_at` and `revoked_at`: when the password was last set
  and when the device was switched off for good.
* A home or tank can now have old, revoked devices plus one active device
  (e.g. after a broken ESP32 is replaced), so "one device per home" becomes
  "one ACTIVE device per home" (partial unique indexes).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('mqtt_password_hash', sa.String(length=160), nullable=True))
    op.add_column(
        'devices', sa.Column('credentials_changed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('devices', sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint('devices_home_id_key', 'devices', type_='unique')
    op.drop_constraint('devices_tank_id_key', 'devices', type_='unique')
    op.create_index(
        'one_active_device_per_home', 'devices', ['home_id'], unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        'one_active_device_per_tank', 'devices', ['tank_id'], unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index('one_active_device_per_tank', table_name='devices')
    op.drop_index('one_active_device_per_home', table_name='devices')
    op.create_unique_constraint('devices_tank_id_key', 'devices', ['tank_id'])
    op.create_unique_constraint('devices_home_id_key', 'devices', ['home_id'])
    op.drop_column('devices', 'revoked_at')
    op.drop_column('devices', 'credentials_changed_at')
    op.drop_column('devices', 'mqtt_password_hash')
