"""Database tables (SRS section 7.1). M3: people and places. M4: devices and readings.
M6: tariffs, bills, payments.

Every table uses UUID keys, so IDs in URLs cannot be guessed by counting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("sysadmin", "owner", "resident")


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="role_valid"),)

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)  # null until an invite is accepted
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class Currency(Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)  # ISO 4217
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    symbol: Mapped[str] = mapped_column(String(8), nullable=False)
    minor_units: Mapped[int] = mapped_column(
        SmallInteger, nullable=False
    )  # 2, or 3 for KWD/BHD/OMR


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = _id()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    nonpayment_shutoff_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    # M6: short code used in bill numbers (B01-2026-000123), given by the
    # database in order: B01, B02 ... (migration 0004).
    code: Mapped[str] = mapped_column(
        String(8), unique=True, nullable=False, server_default=text("next_building_code()")
    )
    # M6: issue new monthly bills automatically instead of leaving them as drafts.
    bills_auto_issue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()

    homes: Mapped[list[Home]] = relationship(back_populates="building")


class Home(Base):
    __tablename__ = "homes"
    __table_args__ = (UniqueConstraint("building_id", "unit_no", name="unit_no_unique"),)

    id: Mapped[uuid.UUID] = _id()
    building_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("buildings.id"), nullable=False, index=True
    )
    unit_no: Mapped[str] = mapped_column(String(20), nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occupants: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    auto_shutoff_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    building: Mapped[Building] = relationship(back_populates="homes")


class Occupancy(Base):
    """A resident living in a home from move_in until move_out (null = still living there)."""

    __tablename__ = "occupancies"
    __table_args__ = (
        CheckConstraint("move_out IS NULL OR move_out >= move_in", name="dates_in_order"),
        # FR-21: at most one active billing contact per home, enforced by the database.
        Index(
            "one_billing_contact_per_home",
            "home_id",
            unique=True,
            postgresql_where=text("is_billing_contact AND move_out IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    move_in: Mapped[date] = mapped_column(Date, nullable=False)
    move_out: Mapped[date | None] = mapped_column(Date)
    is_billing_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Invitation(Base):
    """One-time link for a resident to set a password and join a home (FR-18, FR-21)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = _id()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    move_in: Mapped[date] = mapped_column(Date, nullable=False)
    is_billing_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """Refresh tokens are stored only as a SHA-256 hash. One login = one family (FR-69)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()


class AuditEntry(Base):
    """Append-only audit log (FR-28). Each entry's hash covers the previous hash,
    so changing or removing an old entry breaks the chain (FR-73)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(16))
    ip: Mapped[str | None] = mapped_column(INET)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    building_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)


# ----- M4: devices, readings and commands -----

DEVICE_KINDS = ("home", "tank")
COMMAND_STATES = ("queued", "pending", "succeeded", "failed", "rejected", "timeout")


class Tank(Base):
    """The building's overhead tank. Thresholds and mode are the values the
    tank device last *confirmed*, not just what someone asked for (FR-26)."""

    __tablename__ = "tanks"

    id: Mapped[uuid.UUID] = _id()
    building_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("buildings.id"), nullable=False, unique=True
    )
    capacity_l: Mapped[int] = mapped_column(Integer, nullable=False)
    low_pct: Mapped[float] = mapped_column(Float, nullable=False, default=25.0)
    high_pct: Mapped[float] = mapped_column(Float, nullable=False, default=95.0)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="auto")
    pump_kw: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, default=0.75)


class Device(Base):
    """A home node or a tank node. Only registered, active devices are listened to (FR-22)."""

    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint(f"kind IN {DEVICE_KINDS}", name="kind_valid"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_valid"),
        CheckConstraint(
            "(kind = 'home' AND home_id IS NOT NULL AND tank_id IS NULL)"
            " OR (kind = 'tank' AND tank_id IS NOT NULL AND home_id IS NULL)",
            name="kind_matches_target",
        ),
        # Old, revoked devices stay (their readings and audit refer to them),
        # but a home or tank has at most one ACTIVE device (M5).
        Index(
            "one_active_device_per_home", "home_id", unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "one_active_device_per_tank", "tank_id", unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )  # fmt: skip

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. home-b01-03
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    building_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("buildings.id"), nullable=False, index=True
    )
    home_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("homes.id"))
    tank_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tanks.id"))
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    # FR-14: the MQTT password, hashed for Mosquitto (security/mqtt_passwords.py).
    # NULL = a simulated device that still logs in with its simulator password.
    mqtt_password_hash: Mapped[str | None] = mapped_column(String(160))
    credentials_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fw_version: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Telemetry(Base):
    """One reading from a home node. A TimescaleDB hypertable (see migration 0002)."""

    __tablename__ = "telemetry"
    __table_args__ = (Index("telemetry_home_time", "home_id", "time"),)

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    boot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    home_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    total_l: Mapped[float] = mapped_column(Float, nullable=False)  # the device's counter
    delta_l: Mapped[float] = mapped_column(Float, nullable=False)  # litres since last reading
    flow_lpm: Mapped[float] = mapped_column(Float, nullable=False)
    ph: Mapped[float] = mapped_column(Float, nullable=False)
    temp_c: Mapped[float] = mapped_column(Float, nullable=False)
    valve: Mapped[str] = mapped_column(String(8), nullable=False)
    rssi: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class TankReading(Base):
    """One reading from a tank node. A TimescaleDB hypertable."""

    __tablename__ = "tank_readings"
    __table_args__ = (Index("tank_readings_tank_time", "tank_id", "time"),)

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    boot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    level_pct: Mapped[float | None] = mapped_column(Float)  # null when the sensor failed
    level_l: Mapped[float | None] = mapped_column(Float)
    pump_on: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pump_s: Mapped[float] = mapped_column(Float, nullable=False)  # pump seconds since last reading
    sump_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensor_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dry_run_protect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    overflow_protect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class DeviceCommand(Base):
    """A valve, pump or setting command and what became of it (FR-13, FR-24).

    queued   -> waiting for an earlier command to the same device to finish
    pending  -> sent to the device, waiting for its ack
    succeeded / failed / rejected -> what the device's ack said
    timeout  -> no ack within 15 seconds
    """

    __tablename__ = "device_commands"
    __table_args__ = (
        CheckConstraint(f"state IN {COMMAND_STATES}", name="state_valid"),
        Index("device_commands_open", "device_id", "created_at",
              postgresql_where=text("state IN ('queued', 'pending')")),
    )  # fmt: skip

    id: Mapped[uuid.UUID] = _id()  # = the cmdId the device sees
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), nullable=False)
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    home_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str | None] = mapped_column(String(8))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_role: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    created_at: Mapped[datetime] = _now()
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_reason: Mapped[str | None] = mapped_column(String(24))
    result_state: Mapped[str | None] = mapped_column(String(8))


class ValveLock(Base):
    """Owner lock on a home's valve (FR-27). Kept after unlocking, as history."""

    __tablename__ = "valve_locks"
    __table_args__ = (
        CheckConstraint("locked_state IN ('open', 'closed')", name="locked_state_valid"),
        Index("one_active_lock_per_home", "home_id", unique=True,
              postgresql_where=text("unlocked_at IS NULL")),
    )  # fmt: skip

    id: Mapped[uuid.UUID] = _id()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id"), nullable=False, index=True)
    locked_state: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    locked_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    locked_at: Mapped[datetime] = _now()
    unlocked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlock_reason: Mapped[str | None] = mapped_column(String(300))


class DeviceEvent(Base):
    """Known events, e.g. the leaks the demo backfill put in on purpose (FR-08).
    They are the "ground truth" the leak detector is scored against (M10)."""

    __tablename__ = "device_events"
    __table_args__ = (UniqueConstraint("device_id", "kind", "started_at", name="event_unique"),)

    id: Mapped[uuid.UUID] = _id()
    device_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # backfill | scenario
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ----- M6: tariffs, bills and payments -----
# Money is NUMERIC(14,3) (never float) plus the building's currency (FR-32).

Money = Numeric(14, 3)
BILL_STATES = ("draft", "issued", "partially_paid", "paid", "overdue", "void")
CHARGE_KINDS = ("tax_percent", "extra_percent", "extra_fixed")
PAYMENT_METHODS = ("cash", "bank", "stripe_test", "other")


class Tariff(Base):
    """One VERSION of a building's tariff (FR-33). A change = a new version;
    old versions stay, so issued bills always point at what they used."""

    __tablename__ = "tariffs"
    __table_args__ = (UniqueConstraint("building_id", "version", name="tariff_version_unique"),)

    id: Mapped[uuid.UUID] = _id()
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    # The currency the rates are in. Bills refuse a version in another currency.
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()

    slabs: Mapped[list[TariffSlab]] = relationship(
        order_by="TariffSlab.position", lazy="selectin", cascade="all, delete-orphan"
    )
    charges: Mapped[list[TariffCharge]] = relationship(
        order_by="TariffCharge.position", lazy="selectin", cascade="all, delete-orphan"
    )


class TariffSlab(Base):
    __tablename__ = "tariff_slabs"
    __table_args__ = (CheckConstraint("rate_per_kl >= 0", name="rate_not_negative"),)

    id: Mapped[uuid.UUID] = _id()
    tariff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    from_l: Mapped[int] = mapped_column(Integer, nullable=False)
    to_l: Mapped[int | None] = mapped_column(Integer)  # null = no upper limit
    rate_per_kl: Mapped[Decimal] = mapped_column(Money, nullable=False)  # per 1,000 L


class TariffCharge(Base):
    """A tax or an owner-defined extra charge (FR-33)."""

    __tablename__ = "tariff_charges"
    __table_args__ = (
        CheckConstraint(f"kind IN {CHARGE_KINDS}", name="kind_valid"),
        CheckConstraint("value >= 0", name="value_not_negative"),
    )

    id: Mapped[uuid.UUID] = _id()
    tariff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal] = mapped_column(Money, nullable=False)


class Bill(Base):
    """A monthly water bill for one resident's days in one home (FR-35, FR-37).

    draft -> issued -> partially_paid -> paid, issued/partially_paid -> overdue
    after the due date, and draft/issued/overdue -> void (with a reason). An
    issued bill is never edited: a mistake is fixed by void + a new bill.
    """

    __tablename__ = "bills"
    __table_args__ = (
        CheckConstraint(f"status IN {BILL_STATES}", name="status_valid"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        CheckConstraint("total >= 0 AND amount_paid >= 0", name="amounts_not_negative"),
        CheckConstraint(
            "status = 'draft' OR status = 'void' OR (bill_no IS NOT NULL AND due_date IS NOT NULL)",
            name="issued_bills_have_number_and_due_date",
        ),
        # FR-35 criterion 1: one live bill per home, resident and period, so
        # running the generation twice cannot make duplicates.
        Index("one_bill_per_period", "home_id", "user_id", "period_start", unique=True,
              postgresql_where=text("status <> 'void'")),
        Index("bills_building_month", "building_id", "month"),
    )  # fmt: skip

    id: Mapped[uuid.UUID] = _id()
    bill_no: Mapped[str | None] = mapped_column(String(24), unique=True)  # given when issued
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), nullable=False)
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # Copied when the bill is made, so the bill stays as it was printed.
    bill_to: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_no: Mapped[str] = mapped_column(String(20), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the month
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)  # inclusive
    month_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    litres: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reading_start: Mapped[Decimal | None] = mapped_column(Money)  # meter counter, litres
    reading_end: Mapped[Decimal | None] = mapped_column(Money)
    subtotal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal(0))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    due_date: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(300))
    replaces_bill_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bills.id"))
    # FR-27: when the resident was warned that water may be shut off for this bill.
    shutoff_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    lines: Mapped[list[BillLine]] = relationship(
        order_by="BillLine.position", lazy="selectin", cascade="all, delete-orphan"
    )
    payments: Mapped[list[Payment]] = relationship(
        order_by="Payment.created_at", lazy="selectin", viewonly=True
    )


class BillLine(Base):
    __tablename__ = "bill_lines"
    __table_args__ = (CheckConstraint("kind IN ('slab', 'extra', 'tax')", name="kind_valid"),)

    id: Mapped[uuid.UUID] = _id()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity_l: Mapped[Decimal | None] = mapped_column(Money)
    rate: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tariff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tariffs.id"))


class BillCounter(Base):
    """The last bill number per building and year: B01-2026-000123 (FR-37 crit. 3)."""

    __tablename__ = "bill_counters"

    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_no: Mapped[int] = mapped_column(Integer, nullable=False)


class BillingRun(Base):
    """When the bills of a building and month were last generated (FR-35)."""

    __tablename__ = "billing_runs"

    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Payment(Base):
    """Money received for a bill (FR-39, FR-40). Never deleted: a mistake is
    reversed with a reason, and the reversal is kept."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(f"method IN {PAYMENT_METHODS}", name="method_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = _id()
    bill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bills.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # Stripe's Checkout session ID: unique, so a webhook delivered twice is
    # recorded once (FR-39 criterion 2).
    provider_ref: Mapped[str | None] = mapped_column(String(255), unique=True)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = Stripe
    receipt: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)  # photo, max 2 MB
    receipt_type: Mapped[str | None] = mapped_column(String(16))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()


class CheckoutSession(Base):
    """A Stripe Checkout page we opened for a bill (FR-39). The webhook must
    match one of these, with the same amount and currency."""

    __tablename__ = "checkout_sessions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # cs_test_...
    bill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bills.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
