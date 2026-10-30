"""Demo accounts and the demo building: `make seed` (runs `python -m app.seed`).

Creates, once:
* admin@example.com      System Admin
* owner@example.com      Building Owner of "AquaPulse Demo Tower"
* 10 homes, units 01 to 10 (the same 10 homes the simulator plays)
* resident01@example.com ... resident10@example.com, one per home,
  each the home's billing contact, moved in 3 months ago
* the overhead tank and 11 devices (M4): home-b01-01 ... home-b01-10 for the
  homes and tank-b01-01 for the tank, the same IDs the simulator uses

Every account uses DEMO_PASSWORD from your .env (a random value made by
`make env`), so no password is ever written in the code. Running it again
changes nothing: accounts are skipped if the admin account already exists,
and only missing devices are added (so a building seeded in M3 gets its
devices in M4). Three months of readings come from `make demo-data`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

from sqlalchemy import select

from . import audit, db
from .models import Building, Device, Home, Occupancy, Tank, User
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
        homes = await session.scalars(
            select(Home).where(Home.building_id == building.id).order_by(Home.unit_no)
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


async def seed_all(password: str, timezone: str) -> tuple[bool, int]:
    try:
        return await seed(password, timezone), await seed_devices()
    finally:
        await db.dispose()


def main() -> None:
    password = os.environ.get("DEMO_PASSWORD", "")
    problem = password_problem(password)
    if problem:
        sys.exit(f"DEMO_PASSWORD in .env is not usable: {problem}. Run `make env`.")
    created, devices = asyncio.run(
        seed_all(password, os.environ.get("DEMO_TIMEZONE", "Asia/Karachi"))
    )
    if devices:
        print(f"Added the water tank and {devices} demo devices.")
    if not created:
        print("Demo accounts are already there." + ("" if devices else " Nothing changed."))
        return
    print("Demo data created. Log in at http://localhost:8080/api/docs with:")
    print(f"  {ADMIN} (System Admin), {OWNER} (Building Owner),")
    print(f"  resident01@example.com ... resident{HOMES:02d}@example.com (Residents)")
    print("  password: the DEMO_PASSWORD value in your .env file")


if __name__ == "__main__":
    main()
