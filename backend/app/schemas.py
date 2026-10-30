"""Request and response shapes. Requests reject unknown fields (FR-72)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

CURRENCY_CODES = ("PKR", "USD", "GBP", "EUR", "AUD", "AED", "SAR", "QAR", "KWD", "BHD", "OMR")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Token = Annotated[str, StringConstraints(min_length=20, max_length=200)]


class In(BaseModel):
    """Base for request bodies: unknown fields are an error, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----- auth -----


class LoginIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"email": "owner@example.com", "password": "your DEMO_PASSWORD"}]
        },
    )

    email: EmailStr
    password: Password


class RefreshIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"refresh_token": "the refresh_token from /auth/login"}]},
    )

    refresh_token: Token


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (a type name, not a password)
    expires_in: int = Field(description="Seconds until the access token expires")


class ForgotIn(In):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"email": "resident01@example.com"}]}
    )

    email: EmailStr


class ResetIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"token": "the code from the email", "new_password": "three unrelated words"}
            ]
        },
    )

    token: Token
    new_password: Password


class AcceptInviteIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"token": "the code from the email", "password": "three unrelated words"}]
        },
    )

    token: Token
    password: Password


class ChangePasswordIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"current_password": "old password", "new_password": "three unrelated words"}
            ]
        },
    )

    current_password: Password
    new_password: Password


class MessageOut(BaseModel):
    message: str


# ----- users -----


class UserOut(Out):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    role: str
    is_active: bool


class MeOut(UserOut):
    building_ids: list[uuid.UUID] = []
    home_ids: list[uuid.UUID] = []


class MePatch(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"full_name": "Sana", "phone": "+92 300 1234567"}]},
    )

    full_name: Name | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9 ]{6,20}$")] | None = None


class OwnerIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "email": "new.owner@example.com",
                    "full_name": "New Owner",
                    "password": "three unrelated words",
                }
            ]
        },
    )

    email: EmailStr
    full_name: Name
    password: Password


class OwnerPatch(In):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"is_active": False}]}
    )

    full_name: Name | None = None
    is_active: bool | None = None


# ----- buildings and homes -----


def _check_timezone(value: str | None) -> str | None:
    if value is not None and value not in available_timezones():
        raise ValueError("unknown time zone; use an IANA name like Asia/Karachi")
    return value


class BuildingIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Gulf View Tower",
                    "address": "West Bay, Doha",
                    "timezone": "Asia/Qatar",
                    "currency_code": "QAR",
                }
            ]
        },
    )

    name: Name
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    timezone: str = Field("Asia/Karachi", examples=["Asia/Karachi", "Europe/London"])
    currency_code: Literal[CURRENCY_CODES] = "PKR"  # type: ignore[valid-type]
    owner_id: uuid.UUID | None = Field(
        None, description="System Admin only: which owner gets the building"
    )

    _tz = field_validator("timezone")(_check_timezone)


class BuildingPatch(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "Gulf View Tower 2", "due_days": 20}]},
    )

    name: Name | None = None
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    timezone: str | None = None
    currency_code: Literal[CURRENCY_CODES] | None = None  # type: ignore[valid-type]
    due_days: Annotated[int, Field(ge=1, le=90)] | None = None
    owner_id: uuid.UUID | None = Field(None, description="System Admin only: reassign the owner")

    _tz = field_validator("timezone")(_check_timezone)


class BuildingOut(Out):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    address: str
    timezone: str
    currency_code: str
    nonpayment_shutoff_enabled: bool
    due_days: int
    created_at: datetime


UnitNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class HomeIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"unit_no": "A-101", "floor": 1, "occupants": 3, "notes": ""}]
        },
    )

    unit_no: UnitNo = Field(examples=["A-101"])
    floor: Annotated[int, Field(ge=-5, le=200)] = 0
    occupants: Annotated[int, Field(ge=0, le=50)] = 1
    notes: Annotated[str, StringConstraints(max_length=500)] = ""


class HomePatch(In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"occupants": 4}]})

    unit_no: UnitNo | None = None
    floor: Annotated[int, Field(ge=-5, le=200)] | None = None
    occupants: Annotated[int, Field(ge=0, le=50)] | None = None
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None


class HomeOut(Out):
    id: uuid.UUID
    building_id: uuid.UUID
    unit_no: str
    floor: int
    occupants: int
    notes: str
    archived_at: datetime | None
    created_at: datetime


# ----- residents -----


class InvitationIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "email": "new.resident@example.com",
                    "full_name": "New Resident",
                    "move_in": "2026-11-01",
                    "is_billing_contact": True,
                }
            ]
        },
    )

    email: EmailStr
    full_name: Name
    move_in: date
    is_billing_contact: bool = False


class InvitationOut(BaseModel):
    id: uuid.UUID
    home_id: uuid.UUID
    email: str
    expires_at: datetime
    email_sent: bool


class OccupancyOut(Out):
    id: uuid.UUID
    home_id: uuid.UUID
    user_id: uuid.UUID
    move_in: date
    move_out: date | None
    is_billing_contact: bool


class OccupancyPatch(In):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"move_out": "2026-12-31"}]}
    )

    move_out: date | None = None
    is_billing_contact: bool | None = None


# ----- audit -----


class AuditOut(Out):
    id: int
    at: datetime
    actor_id: uuid.UUID | None
    actor_role: str | None
    ip: str | None
    action: str
    target_type: str | None
    target_id: str | None
    building_id: uuid.UUID | None
    details: dict

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_to_text(cls, value: object) -> str | None:
        return None if value is None else str(value)


class AuditVerifyOut(BaseModel):
    ok: bool
    first_broken_id: int | None


# ----- live data and consumption (M4) -----

Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
# Locks need a real explanation (FR-27): at least 10 characters.
LockReason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]


class ReadingOut(BaseModel):
    time: datetime
    total_l: float = Field(description="The meter's own counter, in litres")
    delta_l: float = Field(description="Litres used since the previous reading")
    flow_lpm: float
    ph: float
    temp_c: float
    valve: str = Field(description="What the device reported: open, closed, moving or fault")
    rssi: int


class ValveLockOut(Out):
    id: uuid.UUID
    home_id: uuid.UUID
    locked_state: str
    reason: str
    locked_at: datetime
    unlocked_at: datetime | None = None
    unlock_reason: str | None = None


class CommandOut(Out):
    id: uuid.UUID = Field(description="The cmdId the device sees")
    device_id: str
    type: str
    action: str | None
    params: dict
    reason: str
    requested_role: str
    state: Literal["queued", "pending", "succeeded", "failed", "rejected", "timeout"]
    created_at: datetime
    issued_at: datetime | None
    acked_at: datetime | None
    result_reason: str | None = Field(description="The device's reason, e.g. BUSY or VALVE_STUCK")
    result_state: str | None = Field(description="State the device reported after the command")


class HomeLiveOut(BaseModel):
    home_id: uuid.UUID
    device_id: str | None
    online: bool
    last_seen_at: datetime | None
    valve: str | None = Field(description="Valve state reported by the device (not just requested)")
    reading: ReadingOut | None
    lock: ValveLockOut | None
    pending_command: CommandOut | None


class ConsumptionPoint(BaseModel):
    start: datetime = Field(description="Start of the hour, day or month, in the building's zone")
    litres: float


class HomeTotal(BaseModel):
    home_id: uuid.UUID
    unit_no: str
    litres: float


class ConsumptionOut(BaseModel):
    granularity: Literal["hour", "day", "month"]
    timezone: str
    from_: date = Field(alias="from")
    to: date
    total_litres: float
    points: list[ConsumptionPoint]
    homes: list[HomeTotal] | None = Field(None, description="Building only: total per home")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class ValveIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"action": "close", "reason": "Going on holiday"}]},
    )

    action: Literal["open", "close"]
    reason: Reason = ""


class ValveLockIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"state": "closed", "reason": "Plumber is fixing the kitchen pipe"}]
        },
    )

    state: Literal["open", "closed"]
    reason: LockReason


class ValveUnlockIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "Repair finished, pipe tested"}]},
    )

    reason: LockReason


# ----- tank (M4) -----


class TankReadingOut(BaseModel):
    time: datetime
    level_pct: float | None = Field(description="null when the level sensor failed")
    level_l: float | None
    pump_on: bool
    sump_ok: bool
    sensor_ok: bool
    dry_run_protect: bool
    overflow_protect: bool
    mode: str


class TankOut(BaseModel):
    id: uuid.UUID
    building_id: uuid.UUID
    capacity_l: int
    low_pct: float = Field(description="Pump starts below this level (confirmed by the device)")
    high_pct: float = Field(description="Pump stops at this level (confirmed by the device)")
    mode: str
    pump_kw: float
    device_id: str | None
    online: bool
    last_seen_at: datetime | None
    reading: TankReadingOut | None
    open_commands: list[CommandOut]


class TankLevelPoint(BaseModel):
    start: datetime
    level_pct: float | None = Field(description="Average level in this period")


class PumpDay(BaseModel):
    day: date
    pump_hours: float
    energy_kwh: float = Field(description="pump_hours x pump_kw (an estimate)")


class TankHistoryOut(BaseModel):
    tank_id: uuid.UUID
    timezone: str
    from_: date = Field(alias="from")
    to: date
    step_minutes: int
    levels: list[TankLevelPoint]
    days: list[PumpDay]

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TankSettingsIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"low_pct": 30, "high_pct": 90, "mode": "auto"}]},
    )

    low_pct: Annotated[float, Field(ge=5, le=87)] | None = None
    high_pct: Annotated[float, Field(ge=15, le=97)] | None = None
    mode: Literal["auto", "manual"] | None = None
    pump_kw: Annotated[float, Field(gt=0, le=50)] | None = None


class TankSettingsOut(BaseModel):
    tank: TankOut
    commands: list[CommandOut] = Field(description="Sent to the device; it confirms each one")


class PumpIn(In):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"action": "on", "reason": "Fill up"}]}
    )

    action: Literal["on", "off"]
    reason: Reason = ""


# ----- live stream (M4) -----


class WsTicketOut(BaseModel):
    ticket: str
    expires_in: int = Field(description="Seconds: open the WebSocket before then")
    url: str = Field(description="Where to connect, e.g. ws://localhost:8080/api/v1/ws?ticket=...")
