"""Device registration, password rotation and revoke (FR-14).

Registering a device creates its ID and a long random password. The API
keeps only a hash (security/mqtt_passwords.py) and NOTIFYs broker-auth, which
puts the new account in the broker's password list: the device can connect
about a second later, without a broker restart. The plain password is in
the response ONCE, also as a QR code, and is never stored or logged.

Rotating gives the device a new password (the old one stops working at once,
and a device still connected with it is disconnected). Revoking switches the
device off for good: it is disconnected and can never log in again. A new
device can then be registered for the same home or tank.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import ssl
import uuid
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Annotated

import segno
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import events as bus
from ..config import get_settings
from ..db import get_session
from ..deps import NOT_FOUND, Caller, require, visible_building, visible_home
from ..models import Device, Tank
from ..schemas import BrokerInfo, DeviceCredentialsOut, DeviceOut
from ..security.mqtt_passwords import mosquitto_hash, new_password

router = APIRouter(tags=["devices"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]

CONFLICT = {409: {"description": "There is already an active device here"}}


@cache
def ca_fingerprint() -> str | None:
    """SHA-256 of the project CA certificate (what `make certs` printed)."""
    path = Path(get_settings().mqtt_ca_file)
    if not path.exists():
        return None
    der = ssl.PEM_cert_to_DER_cert(path.read_text())
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def _credentials(device: Device, password: str) -> DeviceCredentialsOut:
    settings = get_settings()
    broker = BrokerInfo(
        host=settings.mqtt_public_host, port=settings.mqtt_public_port, ca_sha256=ca_fingerprint()
    )
    config = {
        "v": 1,
        "deviceId": device.id,
        "mqtt": {
            "host": broker.host,
            "port": broker.port,
            "tls": True,
            "username": device.id,
            "password": password,
            "caSha256": broker.ca_sha256,
        },
    }
    qr = segno.make(json.dumps(config, separators=(",", ":")), error="m")
    return DeviceCredentialsOut(
        device=DeviceOut.model_validate(device),
        username=device.id,
        password=password,
        broker=broker,
        config=config,
        qr_svg=qr.svg_data_uri(scale=4),
    )


def _set_password(device: Device) -> str:
    """Give the device a new random password. Returns it; stores only the hash."""
    password = new_password()
    device.mqtt_password_hash = mosquitto_hash(password)
    device.credentials_changed_at = datetime.now(UTC)
    return password


async def _record(session: AsyncSession, caller: Caller, action: str, device: Device) -> None:
    await audit.record(
        session,
        action,
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="device",
        target_id=device.id,
        building_id=device.building_id,
        details={"kind": device.kind},  # never the password
    )
    # broker-auth hears this when the transaction commits and updates the broker.
    await bus.notify(session, bus.DEVICES, device.id)


async def _register(session: AsyncSession, caller: Caller, device: Device) -> DeviceCredentialsOut:
    password = _set_password(device)
    session.add(device)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "There is already an active device here. Revoke it first"
        ) from None
    await _record(session, caller, "device.registered", device)
    return _credentials(device, password)


def _new_id(kind: str) -> str:
    """A device ID that fits the MQTT contract, e.g. home-3f9a2c1d-01."""
    return f"{kind}-{secrets.token_hex(4)}-01"


async def _own_device(session: AsyncSession, caller: Caller, device_id: str) -> Device:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await visible_building(session, caller, device.building_id)  # 404 if not yours
    return device


@router.get("/buildings/{building_id}/devices", response_model=list[DeviceOut], responses={404: {}})
async def list_devices(building_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Every device of the building, revoked ones included."""
    await visible_building(session, caller, building_id)
    return list(
        await session.scalars(
            select(Device).where(Device.building_id == building_id).order_by(Device.id)
        )
    )


@router.post(
    "/homes/{home_id}/device",
    response_model=DeviceCredentialsOut,
    status_code=201,
    responses={404: {}, **CONFLICT},
)
async def register_home_device(home_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Register the meter/valve node of a home. The password is shown only once."""
    home = await visible_home(session, caller, home_id, manage=True)
    if home.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This home is archived")
    device = Device(id=_new_id("home"), kind="home", building_id=home.building_id, home_id=home.id)
    return await _register(session, caller, device)


@router.post(
    "/tanks/{tank_id}/device",
    response_model=DeviceCredentialsOut,
    status_code=201,
    responses={404: {}, **CONFLICT},
)
async def register_tank_device(tank_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Register the building's tank node. The password is shown only once."""
    tank = await session.get(Tank, tank_id)
    if tank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await visible_building(session, caller, tank.building_id)
    device = Device(id=_new_id("tank"), kind="tank", building_id=tank.building_id, tank_id=tank.id)
    return await _register(session, caller, device)


@router.post(
    "/devices/{device_id}/password",
    response_model=DeviceCredentialsOut,
    responses={404: {}, 409: {}},
)
async def rotate_password(device_id: str, session: Session, caller: OwnerOnly):
    """Give the device a new password. The old one is refused within seconds."""
    device = await _own_device(session, caller, device_id)
    if device.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "This device is revoked")
    password = _set_password(device)
    await _record(session, caller, "device.password_rotated", device)
    return _credentials(device, password)


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut, responses={404: {}})
async def revoke_device(device_id: str, session: Session, caller: OwnerOnly):
    """Switch the device off for good: it is disconnected and can never log in again."""
    device = await _own_device(session, caller, device_id)
    if device.status != "revoked":
        device.status = "revoked"
        device.revoked_at = datetime.now(UTC)
        device.mqtt_password_hash = None
        device.online = False
        await _record(session, caller, "device.revoked", device)
    return device
