"""System Admin accounts, from the server's command line (M8, FR-55, FR-70).

There is no screen that makes a System Admin: whoever can run commands on the
server is already in charge, so that is where it happens.

    make admin EMAIL=you@example.com        (asks for a password)
    make admin-reset-2fa EMAIL=you@example.com

`add` makes a new System Admin. They must turn on two-factor login the first
time they log in (FR-70). `reset-2fa` is the way back in for an admin who lost
their phone and their recovery codes: it switches their two-factor login off
and logs them out everywhere, so they set it up again at their next login.
Both are written to the audit log.

The password comes from ADMIN_PASSWORD if it is set (used by the browser
tests), otherwise it is asked for and never shown.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from sqlalchemy import delete, select

from . import audit, db
from .models import RecoveryCode, User
from .routers.auth import revoke_all_sessions
from .security.passwords import hash_password, password_problem


async def add(email: str, full_name: str, password: str) -> str:
    async with db.sessions()() as session, session.begin():
        if await session.scalar(select(User).where(User.email == email)):
            return f"{email} already has an account. Nothing changed."
        user = User(email=email, full_name=full_name, role="sysadmin",
                    password_hash=hash_password(password))  # fmt: skip
        session.add(user)
        await session.flush()
        await audit.record(session, "admin.sysadmin_created", target_type="user",
                           target_id=user.id, details={"via": "command line"})  # fmt: skip
    return f"System Admin {email} created. They set up two-factor login at their first login."


async def reset_two_factor(email: str) -> str:
    async with db.sessions()() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == email))
        if user is None or user.role != "sysadmin":
            return f"No System Admin with the email {email}."
        user.totp_enabled_at = None
        user.totp_secret = None
        user.totp_last_step = None
        await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        await revoke_all_sessions(session, user.id)
        await audit.record(session, "admin.2fa_reset", target_type="user", target_id=user.id,
                           details={"via": "command line"})  # fmt: skip
    return f"Two-factor login reset for {email}. They set it up again at their next login."


def _password(email: str) -> str:
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password for the new admin: ")
    if problem := password_problem(password, email):
        sys.exit(f"That password is not allowed: {problem}")
    return password


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] not in ("add", "reset-2fa"):
        sys.exit("Use: python -m app.admins add EMAIL | reset-2fa EMAIL")
    command, email = argv[0], argv[1].strip().lower()
    password = _password(email) if command == "add" else ""
    print(asyncio.run(_run(command, email, password)))


async def _run(command: str, email: str, password: str) -> str:
    try:
        if command == "add":
            return await add(email, "System Admin", password)
        return await reset_two_factor(email)
    finally:
        await db.dispose()


if __name__ == "__main__":
    main(sys.argv[1:])
