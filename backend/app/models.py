"""Database tables for M3 (SRS section 7.1). Later milestones add more.

Every table uses UUID keys, so IDs in URLs cannot be guessed by counting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
