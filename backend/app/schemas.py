"""Request and response shapes. Requests reject unknown fields (FR-72)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
    field_serializer,
    field_validator,
)

# The currencies themselves are rows of the `currencies` table (FR-32 crit. 3):
# the API checks a code against that table, so a new currency is one new row.
CurrencyCode = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{3}$"), Field(examples=["PKR", "KWD"])
]

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
    code: str | None = Field(
        None,
        max_length=20,
        description="Only with two-factor login on (FR-70): the 6-digit code, or a recovery code",
    )


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
    two_factor: bool = Field(False, description="Two-factor login is on (FR-70)")


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
    currency_code: CurrencyCode = "PKR"
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
    currency_code: CurrencyCode | None = Field(
        None, description="Cannot change once the building has bills (FR-20)"
    )
    due_days: Annotated[int, Field(ge=1, le=90)] | None = None
    nonpayment_shutoff_enabled: bool | None = Field(
        None,
        description="Allow closing the water for an overdue bill, after a 48 h warning (FR-27)",
    )
    bills_auto_issue: bool | None = Field(
        None, description="Issue monthly bills at once instead of leaving them as drafts"
    )
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
    code: str = Field(description="Used in bill numbers, e.g. B01-2026-000123")
    bills_auto_issue: bool
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
    model_config = ConfigDict(from_attributes=True)

    ok: bool
    first_broken_id: int | None
    entries: int = Field(description="How many entries were checked")
    seconds: float = Field(description="How long the check took")
    at: datetime
    requested_by: uuid.UUID | None = Field(description="null = the daily check by the worker")


class NotificationOut(BaseModel):
    """One email (FR-60). The address and the text are not shown here."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    kind: str
    alert_id: uuid.UUID | None
    bill_id: uuid.UUID | None
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


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
    unit_no: str = ""
    building_name: str = ""
    ph_safe_low: float = Field(6.5, description="Below this the water is unsafe (alert rules)")
    ph_safe_high: float = Field(8.5, description="Above this the water is unsafe")


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
    bill_id: uuid.UUID | None = Field(
        None,
        description="Only for a shut-off because of an unpaid bill (FR-27): the building's "
        "policy must allow it, the bill must be overdue and the resident warned 48 h before",
    )


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
    url: str = Field(description="Where to connect, e.g. wss://localhost:8443/api/v1/ws?ticket=...")


# ----- devices and their MQTT passwords (M5) -----


class DeviceOut(Out):
    id: str = Field(description="Also the device's MQTT username")
    kind: Literal["home", "tank"]
    building_id: uuid.UUID
    home_id: uuid.UUID | None
    tank_id: uuid.UUID | None
    status: Literal["active", "revoked"]
    online: bool
    fw_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    credentials_changed_at: datetime | None
    revoked_at: datetime | None
    mqtt_password_hash: str | None = Field(None, exclude=True)  # never sent, only read below

    @computed_field(  # type: ignore[prop-decorator]
        description="False = a simulated device that still uses its simulator password"
    )
    @property
    def has_own_password(self) -> bool:
        return self.mqtt_password_hash is not None


class BrokerInfo(BaseModel):
    host: str
    port: int
    tls: bool = True
    ca_sha256: str | None = Field(
        description="SHA-256 fingerprint of the project CA: the device checks the broker with it"
    )


class DeviceCredentialsOut(BaseModel):
    """Shown ONCE. Only a hash is kept, so a lost password cannot be shown
    again: rotate it instead (FR-14)."""

    device: DeviceOut
    username: str
    password: str = Field(description="Shown only this once. Copy it into the device now")
    broker: BrokerInfo
    config: dict = Field(description="Everything the device needs, as one JSON object (in the QR)")
    qr_svg: str = Field(description="The config as a QR code (an SVG image as a data: URL)")


# ----- billing (M6) -----
# Money is sent as a string ("1250.00"), never as a JSON number, so no app
# ever turns it into a rounded floating-point value.

Litres = Annotated[int, Field(ge=0, le=100_000_000)]
Rate = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]
Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
LongReason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]


class SlabIn(In):
    from_l: Litres = Field(description="Litres where the slab starts (the first slab: 0)")
    to_l: Litres | None = Field(description="Where it ends; null for the last slab (no limit)")
    rate_per_kl: Rate = Field(description="Price per 1,000 litres, in the building's currency")


class ChargeIn(In):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    kind: Literal["tax_percent", "extra_percent", "extra_fixed"] = Field(
        description="tax_percent: % of water + extras; extra_percent: % of water; "
        "extra_fixed: a fixed amount per month (prorated for part months)"
    )
    value: Rate


class TariffRules(In):
    slabs: list[SlabIn] = Field(min_length=1, max_length=10)
    charges: list[ChargeIn] = Field(default_factory=list, max_length=10)


class TariffIn(TariffRules):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Standard 2026",
                    "effective_from": "2026-11-01",
                    "slabs": [
                        {"from_l": 0, "to_l": 5000, "rate_per_kl": "60"},
                        {"from_l": 5000, "to_l": 15000, "rate_per_kl": "120"},
                        {"from_l": 15000, "to_l": None, "rate_per_kl": "200"},
                    ],
                    "charges": [{"name": "Sewerage", "kind": "extra_percent", "value": "10"}],
                }
            ]
        },
    )

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    effective_from: date


class TariffPreviewIn(TariffRules):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "currency_code": "PKR",
                    "litres": "7000",
                    "slabs": [
                        {"from_l": 0, "to_l": 5000, "rate_per_kl": "60"},
                        {"from_l": 5000, "to_l": None, "rate_per_kl": "120"},
                    ],
                    "charges": [],
                }
            ]
        },
    )

    currency_code: CurrencyCode
    litres: Annotated[Decimal, Field(ge=0, le=100_000_000, decimal_places=3)]
    days: Annotated[int, Field(ge=1, le=31)] = 30
    month_days: Annotated[int, Field(ge=28, le=31)] = 30


def _plain(value: Decimal) -> str:
    """60.000 -> "60", 0.250 -> "0.25": rates as people write them."""
    return f"{value.normalize():f}"


class SlabOut(Out):
    from_l: int
    to_l: int | None
    rate_per_kl: Decimal

    _rate = field_serializer("rate_per_kl")(lambda self, v: _plain(v))


class ChargeOut(Out):
    name: str
    kind: str
    value: Decimal

    _value = field_serializer("value")(lambda self, v: _plain(v))


class TariffOut(Out):
    id: uuid.UUID
    building_id: uuid.UUID
    name: str
    version: int
    effective_from: date
    currency_code: str
    created_at: datetime
    slabs: list[SlabOut]
    charges: list[ChargeOut]


class BillLineOut(BaseModel):
    kind: Literal["slab", "extra", "tax"]
    description: str
    quantity_l: Decimal | None
    rate: Decimal | None = Field(description="Slabs: per 1,000 L. Taxes and extras: a percentage")
    amount: Decimal


class PreviewOut(BaseModel):
    currency_code: str
    litres: Decimal
    lines: list[BillLineOut]
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    total_text: str = Field(description="Formatted for people, e.g. Rs 1,250.00 or KWD 4.375")


class PaymentOut(BaseModel):
    id: uuid.UUID
    bill_id: uuid.UUID
    amount: Decimal
    method: Literal["cash", "bank", "stripe_test", "other"]
    reference: str
    received_on: date
    has_receipt: bool
    created_at: datetime
    reversed_at: datetime | None
    reversal_reason: str | None


class BillSummaryOut(BaseModel):
    id: uuid.UUID
    bill_no: str | None = Field(description="Given when the bill is issued")
    building_id: uuid.UUID
    home_id: uuid.UUID
    user_id: uuid.UUID
    bill_to: str
    unit_no: str
    month: date
    period_start: date
    period_end: date
    currency_code: str
    litres: Decimal
    total: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    total_text: str
    status: Literal["draft", "issued", "partially_paid", "paid", "overdue", "void"]
    due_date: date | None
    issued_at: datetime | None


class UnpaidTotalOut(BaseModel):
    currency_code: str
    bills: int
    amount: Decimal = Field(description="Still to pay")
    overdue_amount: Decimal = Field(description="Of which past the due date")
    amount_text: str


class UnpaidOut(BaseModel):
    bills: list[BillSummaryOut]
    totals: list[UnpaidTotalOut] = Field(description="One line per currency (FR-38)")


class BillOut(BillSummaryOut):
    month_days: int
    reading_start: Decimal | None
    reading_end: Decimal | None
    subtotal: Decimal
    tax_total: Decimal
    lines: list[BillLineOut]
    payments: list[PaymentOut]
    void_reason: str | None
    replaces_bill_id: uuid.UUID | None
    shutoff_warned_at: datetime | None
    can_pay_online: bool = Field(description="Show the 'Pay online (test mode)' button (FR-39)")


class GenerateIn(In):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"month": "2026-09"}]}
    )

    month: Month


class GenerateOut(BaseModel):
    month: date
    created: int = Field(description="New draft bills")
    updated: int = Field(description="Drafts recalculated")
    replaced: int = Field(description="New drafts for voided bills (also counted in created)")
    kept: int = Field(description="Bills already issued: never changed")
    issued: int = Field(description="Issued at once (building setting bills_auto_issue)")
    problems: list[str]


class IssueAllOut(BaseModel):
    issued: int
    bills: list[BillSummaryOut]


class VoidIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "Wrong tariff: new tariff starts in October"}]},
    )

    reason: LongReason


class PaymentIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"amount": "500.00", "method": "cash", "reference": "Receipt book #12"}]
        },
    )

    amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
    method: Literal["cash", "bank", "other"]
    received_on: date | None = Field(None, description="Default: today")
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class ReverseIn(In):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "Recorded on the wrong bill"}]},
    )

    reason: LongReason


class CheckoutOut(BaseModel):
    url: str = Field(description="Open this page to pay (Stripe Checkout)")
    session_id: str
    amount: Decimal = Field(
        description="What Stripe will charge (3-decimal currencies: up to 9 fils more)"
    )
    currency_code: str
    test_mode: bool = True
    note: str = "Test mode, no real money. Test card: 4242 4242 4242 4242"


# ----- alerts and notifications (M7) -----


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule: str
    severity: Literal["info", "warning", "critical"]
    state: Literal["open", "acknowledged", "resolved", "auto_resolved"]
    building_id: uuid.UUID | None = Field(description="null = a system alert")
    home_id: uuid.UUID | None
    tank_id: uuid.UUID | None
    device_id: str | None
    title: str
    message: str
    value: float | None = Field(description="The reading that last matched the rule")
    seen_count: int = Field(description="How many times the problem was seen (de-duplicated)")
    opened_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None = Field(
        description="Problem gone since (auto-resolved 10 min later)"
    )
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None


class AlertResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field("", max_length=300, description="What was done, for the audit log")


Recipient = Literal["owner", "resident"]


class AlertRulesIn(BaseModel):
    """Only what you send changes, e.g. {"rules": {"ph_unsafe": {"low": 6.8}}}."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "rules": {"ph_unsafe": {"low": 6.8, "high": 8.2}, "high_usage": {"enabled": False}},
                "recipients": {"info": ["owner"], "warning": ["owner", "resident"]},
            }
        },
    )

    rules: dict[str, dict[str, float | int | bool]] = Field(default_factory=dict)
    recipients: dict[Literal["info", "warning", "critical"], list[Recipient]] | None = None


class AlertRuleOut(BaseModel):
    rule: str
    title: str
    severity: str
    about: str = Field(description="home, tank or device")
    description: str
    enabled: bool
    values: dict[str, float]
    limits: dict[str, dict[str, float | str]]


class AlertRulesOut(BaseModel):
    rules: list[AlertRuleOut]
    recipients: dict[str, list[str]] = Field(description="Who is emailed for each severity")


class NotificationPrefsIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "alert_email_min": "warning",
                    "email_muted_rules": ["high_usage"],
                    "quiet_from": "22:00",
                    "quiet_until": "07:00",
                }
            ]
        },
    )

    alert_email_min: Literal["info", "warning", "critical", "off"] | None = None
    email_muted_rules: list[str] | None = Field(
        None, description="Alert types you never want by email (in the app they still show)"
    )
    quiet_from: time | None = Field(None, description="Quiet hours start (building time)")
    quiet_until: time | None = Field(None, description="Quiet hours end")


class NotificationPrefsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_email_min: str = Field(description="Alerts of this severity and above also by email")
    email_muted_rules: list[str] = []
    quiet_from: time | None = Field(
        None, description="Non-critical emails wait until quiet hours end (FR-52)"
    )
    quiet_until: time | None = None


# ----- M8: the resident's screens and two-factor login -----


class ComparisonOut(BaseModel):
    month: date
    until: date = Field(description="Last day counted (today for this month)")
    litres: float
    previous_month_litres: float | None = Field(
        None, description="None if you did not live here last month"
    )
    people_in_home: int
    litres_per_person: float | None
    building_litres_per_person: float | None = Field(
        None, description="Only shown with 3 or more occupied homes (FR-49)"
    )
    occupied_homes: int


class BillEstimateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    month: date
    from_: date = Field(alias="from")
    to: date
    currency_code: str
    litres: float | None = None
    total: Decimal | None = None
    total_text: str | None = None
    message: str = ""


class TwoFactorSetupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=200)


class TwoFactorSetupOut(BaseModel):
    secret: str = Field(description="Type this into the authenticator app if you cannot scan")
    otpauth_url: str
    qr_svg: str = Field(description="The same as a QR code (SVG) to scan")


class TwoFactorCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]


class TwoFactorDisableIn(TwoFactorCodeIn):
    password: str = Field(max_length=200)


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str] = Field(description="Shown once. Each works one time.")
