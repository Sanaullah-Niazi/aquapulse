"""Demo accounts and the demo building: `make seed` (runs `python -m app.seed`).

Creates, once:
* admin@example.com      System Admin
* owner@example.com      Building Owner of "AquaPulse Demo Tower"
* 10 homes, units 01 to 10 (the same 10 homes the simulator plays)
* resident01@example.com ... resident10@example.com, one per home,
  each the home's billing contact, moved in 3 months ago

Every account uses DEMO_PASSWORD from your .env (a random value made by
`make env`), so no password is ever written in the code. Running it again
changes nothing: it stops if the admin account already exists.
The three months of water readings arrive with the ingestor in M4.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

from sqlalchemy import select

from . import audit, db
from .models import Building, Home, Occupancy, User
from .security.passwords import hash_password, password_problem

ADMIN = "admin@example.com"
OWNER = "owner@example.com"
HOMES = 10


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
    await db.dispose()
    return True


def main() -> None:
    password = os.environ.get("DEMO_PASSWORD", "")
    problem = password_problem(password)
    if problem:
        sys.exit(f"DEMO_PASSWORD in .env is not usable: {problem}. Run `make env`.")
    created = asyncio.run(seed(password, os.environ.get("DEMO_TIMEZONE", "Asia/Karachi")))
    if not created:
        print("Demo data is already there. Nothing changed.")
        return
    print("Demo data created. Log in at http://localhost:8080/api/docs with:")
    print(f"  {ADMIN} (System Admin), {OWNER} (Building Owner),")
    print(f"  resident01@example.com ... resident{HOMES:02d}@example.com (Residents)")
    print("  password: the DEMO_PASSWORD value in your .env file")


if __name__ == "__main__":
    main()
