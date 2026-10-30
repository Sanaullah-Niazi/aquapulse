"""Demo accounts and the demo building: `make seed` (runs `python -m app.seed`).

Creates, once:
* admin@example.com      System Admin
* owner@example.com      Building Owner of "AquaPulse Demo Tower"
* 10 homes, units 01 to 10 (the same 10 homes the simulator plays)
* resident01@example.com ... resident10@example.com, one per home,
  each the home's billing contact, moved in 3 months ago
* the overhead tank and 11 devices (M4): home-b01-01 ... home-b01-10 for the
  homes and tank-b01-01 for the tank, the same IDs the simulator uses
* M6: a PKR tariff for it (Rs 60 / 120 / 200 per 1,000 L), and a second
  building "AquaPulse Demo Gulf" in KWD (3 decimals) with 3 homes; in unit
  03 one resident moves out on the 15th of last month and another moves in
  on the 16th, to show prorated bills. Its readings: `make demo-data`.

Every account uses DEMO_PASSWORD from your .env (a random value made by
`make env`), so no password is ever written in the code. Running it again
changes nothing: accounts are skipped if the admin account already exists,
and only missing devices are added (so a building seeded in M3 gets its
devices in M4). Three months of readings come from `make demo-data`.

`python -m app.seed --sim-passwords` (`make sim-passwords`, M5) gives the
active demo devices back their simulator passwords, e.g. after you rotated
one in Swagger to try FR-14. Revoked devices stay revoked.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from . import audit, db
from . import events as bus
from .models import (
    Building,
    Device,
    Home,
    Occupancy,
    Tank,
    Tariff,
    TariffCharge,
    TariffSlab,
    User,
)
from .security.passwords import hash_password, password_problem

ADMIN = "admin@example.com"
OWNER = "owner@example.com"
HOMES = 10
BUILDING_CODE = "b01"  # the simulator's SIM_BUILDING
TANK_CAPACITY_L = 5000  # the simulator's SIM_TANK_CAPACITY_L


async def seed(password: str, timezone: str) -> bool:
    """Returns False if the demo data was already there."""
    pw_hash = hash_password(password)  # one hash for all demo users: Argon2id is slow on purpose
    async with db.sessions()() as session, session.begin():
        if await session.scalar(select(User).where(User.email == ADMIN)):
            return False
        admin = User(email=ADMIN, full_name="Demo Admin", role="sysadmin", password_hash=pw_hash)
        owner = User(email=OWNER, full_name="Demo Owner", role="owner", password_hash=pw_hash)
        session.add_all([admin, owner])
        await session.flush()

        building = Building(
            owner_id=owner.id,
            name="AquaPulse Demo Tower",
            address="1 Demo Street",
            timezone=timezone,
            currency_code="PKR",
        )
        session.add(building)
        await session.flush()

        move_in = date.today() - timedelta(days=90)
        for n in range(1, HOMES + 1):
            home = Home(
                building_id=building.id, unit_no=f"{n:02d}", floor=(n - 1) // 4, occupants=2 + n % 4
            )
            resident = User(
                email=f"resident{n:02d}@example.com",
                full_name=f"Resident of unit {n:02d}",
                role="resident",
                password_hash=pw_hash,
            )
            session.add_all([home, resident])
            await session.flush()
            session.add(
                Occupancy(
                    home_id=home.id, user_id=resident.id, move_in=move_in, is_billing_contact=True
                )
            )

        await audit.record(
            session,
            "seed.demo_data",
            target_type="building",
            target_id=building.id,
            building_id=building.id,
            details={"homes": HOMES},
        )
    return True


async def seed_devices() -> int:
    """Add the tank and the 11 demo devices if they are missing. Returns how many were added."""
    added = 0
    async with db.sessions()() as session, session.begin():
        owner = await session.scalar(select(User).where(User.email == OWNER))
        building = owner and await session.scalar(
            select(Building).where(Building.owner_id == owner.id).order_by(Building.created_at)
        )
        if building is None:
            return 0
        tank = await session.scalar(select(Tank).where(Tank.building_id == building.id))
        if tank is None:
            tank = Tank(building_id=building.id, capacity_l=TANK_CAPACITY_L)
            session.add(tank)
            await session.flush()
        existing = set(await session.scalars(select(Device.id)))
        # Only the 10 demo homes (not homes you or the smoke test added).
        homes = await session.scalars(
            select(Home)
            .where(
                Home.building_id == building.id,
                Home.unit_no.in_([f"{n:02d}" for n in range(1, HOMES + 1)]),
                Home.archived_at.is_(None),
            )
            .order_by(Home.unit_no)
        )
        wanted = [
            Device(id=f"home-{BUILDING_CODE}-{h.unit_no}", kind="home",
                   building_id=building.id, home_id=h.id)
            for h in homes
        ] + [
            Device(id=f"tank-{BUILDING_CODE}-01", kind="tank",
                   building_id=building.id, tank_id=tank.id)
        ]  # fmt: skip
        for device in wanted:
            if device.id not in existing:
                session.add(device)
                added += 1
        if added:
            await audit.record(
                session,
                "seed.demo_devices",
                target_type="building",
                target_id=building.id,
                building_id=building.id,
                details={"devices": added},
            )
    return added


async def reset_simulator_passwords() -> int:
    """Remove the own password of the active demo devices, so broker-auth lets them
    log in with their simulator password again. Returns how many changed."""
    async with db.sessions()() as session, session.begin():
        devices = list(
            await session.scalars(
                select(Device).where(
                    Device.id.like(f"%-{BUILDING_CODE}-%"),
                    Device.status == "active",
                    Device.mqtt_password_hash.is_not(None),
                )
            )
        )
        for device in devices:
            device.mqtt_password_hash = None
            await audit.record(
                session,
                "seed.simulator_password",
                target_type="device",
                target_id=device.id,
                building_id=device.building_id,
            )
            await bus.notify(session, bus.DEVICES, device.id)
    await db.dispose()
    return len(devices)


# ----- M6: tariffs and a second demo building in KWD -----

GULF = "AquaPulse Demo Gulf"
GULF_CODE = "b02"  # device IDs home-b02-01 ...; its history: make demo-data
GULF_HOMES = 3
PKR_SLABS = [(0, 5000, "60"), (5000, 15000, "120"), (15000, None, "200")]  # SRS OQ-5
KWD_SLABS = [(0, 5000, "0.250"), (5000, 15000, "0.500"), (15000, None, "0.800")]


def demo_tariff(building: Building, slabs, charges=()) -> Tariff:
    # Starts well before the 90 days of demo history, so every month has a tariff.
    start = (date.today() - timedelta(days=150)).replace(day=1)
    return Tariff(
        building_id=building.id,
        name="Demo tariff",
        version=1,
        effective_from=start,
        currency_code=building.currency_code,
        slabs=[
            TariffSlab(position=n, from_l=a, to_l=b, rate_per_kl=Decimal(rate))
            for n, (a, b, rate) in enumerate(slabs)
        ],
        charges=[
            TariffCharge(position=n, name=name, kind=kind, value=Decimal(value))
            for n, (name, kind, value) in enumerate(charges)
        ],
    )


async def seed_billing(password: str) -> list[str]:
    """Add the PKR tariff to the demo building, and the KWD demo building with
    3 homes (unit 03 changes residents mid-month, to show prorated bills).
    Returns what was added."""
    added: list[str] = []
    async with db.sessions()() as session, session.begin():
        owner = await session.scalar(select(User).where(User.email == OWNER))
        if owner is None:
            return added
        demo = await session.scalar(
            select(Building).where(Building.owner_id == owner.id, Building.currency_code == "PKR")
            .order_by(Building.created_at)
        )  # fmt: skip
        if demo is not None and not await session.scalar(
            select(Tariff.id).where(Tariff.building_id == demo.id)
        ):
            session.add(demo_tariff(demo, PKR_SLABS))
            added.append(f"PKR tariff for {demo.name}")

        if await session.scalar(select(Building).where(Building.name == GULF)):
            return added
        gulf = Building(
            owner_id=owner.id,
            name=GULF,
            address="Demo Street, Kuwait City",
            timezone="Asia/Kuwait",
            currency_code="KWD",
        )
        session.add(gulf)
        await session.flush()
        session.add(demo_tariff(gulf, KWD_SLABS, [("Sewerage", "extra_percent", "10")]))
        tank = Tank(building_id=gulf.id, capacity_l=TANK_CAPACITY_L)
        session.add(tank)
        await session.flush()
        session.add(
            Device(id=f"tank-{GULF_CODE}-01", kind="tank", building_id=gulf.id, tank_id=tank.id)
        )

        pw_hash = hash_password(password)
        today = date.today()
        last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        long_ago = today - timedelta(days=90)
        for n in range(1, GULF_HOMES + 1):
            home = Home(building_id=gulf.id, unit_no=f"{n:02d}", floor=n - 1, occupants=3)
            session.add(home)
            await session.flush()
            session.add(Device(id=f"home-{GULF_CODE}-{n:02d}", kind="home",
                               building_id=gulf.id, home_id=home.id))  # fmt: skip
            # Unit 03: the first resident leaves on the 15th of last month, the
            # next one moves in on the 16th. Each gets a bill for their own days.
            stays = (
                [("a", long_ago, last_month + timedelta(days=14)),
                 ("b", last_month + timedelta(days=15), None)]
                if n == GULF_HOMES else [("", long_ago, None)]
            )  # fmt: skip
            for suffix, move_in, move_out in stays:
                resident = User(
                    email=f"gulf{n:02d}{suffix}@example.com",
                    full_name=f"Gulf resident {n:02d}{suffix.upper()}",
                    role="resident",
                    password_hash=pw_hash,
                )
                session.add(resident)
                await session.flush()
                session.add(
                    Occupancy(home_id=home.id, user_id=resident.id, move_in=move_in,
                              move_out=move_out, is_billing_contact=True)
                )  # fmt: skip
        await audit.record(
            session,
            "seed.demo_billing",
            target_type="building",
            target_id=gulf.id,
            building_id=gulf.id,
            details={"homes": GULF_HOMES, "currency": "KWD"},
        )
        added.append(f"{GULF} (KWD, {GULF_HOMES} homes, residents gulf01@example.com ...)")
    return added


async def seed_all(password: str, timezone: str) -> tuple[bool, int, list[str]]:
    try:
        created = await seed(password, timezone)
        devices = await seed_devices()
        return created, devices, await seed_billing(password)
    finally:
        await db.dispose()


def main() -> None:
    if "--sim-passwords" in sys.argv:
        changed = asyncio.run(reset_simulator_passwords())
        print(f"{changed} demo device(s) use their simulator password again.")
        return
    password = os.environ.get("DEMO_PASSWORD", "")
    problem = password_problem(password)
    if problem:
        sys.exit(f"DEMO_PASSWORD in .env is not usable: {problem}. Run `make env`.")
    created, devices, billing = asyncio.run(
        seed_all(password, os.environ.get("DEMO_TIMEZONE", "Asia/Karachi"))
    )
    if devices:
        print(f"Added the water tank and {devices} demo devices.")
    for item in billing:
        print(f"Added {item}.")
    if not created:
        changed = devices or billing
        print("Demo accounts are already there." + ("" if changed else " Nothing changed."))
        return
    print("Demo data created. Log in at https://localhost:8443/api/docs with:")
    print(f"  {ADMIN} (System Admin), {OWNER} (Building Owner),")
    print(f"  resident01@example.com ... resident{HOMES:02d}@example.com (Residents)")
    print("  password: the DEMO_PASSWORD value in your .env file")


if __name__ == "__main__":
    main()
