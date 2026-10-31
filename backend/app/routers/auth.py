"""Login, token refresh, logout, password reset, invitations (FR-18, FR-68, FR-69)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import Settings, get_settings
from ..db import get_session
from ..deps import Caller, client_ip, current_caller
from ..emailer import send_email
from ..models import Home, Invitation, Occupancy, PasswordReset, RecoveryCode, RefreshToken, User
from ..schemas import (
    AcceptInviteIn,
    ForgotIn,
    LoginIn,
    MessageOut,
    RefreshIn,
    ResetIn,
    TokensOut,
)
from ..security import totp
from ..security.passwords import hash_password, needs_rehash, password_problem, verify_password
from ..security.ratelimit import SlidingWindow
from ..security.tokens import create_access_token, hash_opaque_token, new_opaque_token


def email_fingerprint(email: str) -> str:
    """The audit log cannot be changed later (FR-73), so it never keeps an email
    address (FR-75, NFR-15): only a short fingerprint, enough to see that the
    same address was tried many times."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


router = APIRouter(prefix="/auth", tags=["auth"])

# FR-68: 5 failed logins per 15 minutes, counted per account and per IP address.
# Attempts made while blocked count too, so someone who keeps guessing stays
# blocked until 15 minutes after their *last* try (the "growing delay").
login_failures_by_account = SlidingWindow(limit=5, window_s=15 * 60, max_events=20)
login_failures_by_ip = SlidingWindow(limit=5, window_s=15 * 60, max_events=20)

BAD_LOGIN = "Wrong email or password"  # the same text whether or not the email exists
NEED_CODE = "Two-factor code required"
BAD_CODE = "Wrong two-factor code"
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
Config = Annotated[Settings, Depends(get_settings)]


def _now() -> datetime:
    return datetime.now(UTC)


async def issue_tokens(
    session: AsyncSession, settings: Settings, user: User, family: uuid.UUID | None = None
) -> TokensOut:
    """Create a refresh token (new session, or the next one in a family) + access token."""
    family = family or uuid.uuid4()
    token, token_hash = new_opaque_token()
    record = RefreshToken(
        user_id=user.id,
        family_id=family,
        token_hash=token_hash,
        expires_at=_now() + timedelta(days=settings.refresh_token_days),
    )
    session.add(record)
    await session.flush()
    return TokensOut(
        access_token=create_access_token(settings, user.id, user.role, family),
        refresh_token=token,
        expires_in=settings.access_token_minutes * 60,
    )


async def _second_factor(
    session: AsyncSession, settings: Settings, user: User, code: str | None
) -> str | None:
    """FR-70: None if the 6-digit code (or a recovery code) is right, else why not."""
    if user.totp_enabled_at is None:
        return None
    if not code:
        return NEED_CODE
    code = code.strip()
    if len(code) == 6 and code.isdigit():
        step = totp.matching_step(totp.decrypt(settings, user.totp_secret or ""), code,
                                  user.totp_last_step)  # fmt: skip
        if step is None:
            return BAD_CODE
        user.totp_last_step = step  # the same code never works twice
        return None
    hashes = totp.recovery_hashes(settings, code)
    found = await session.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash.in_(hashes),
            RecoveryCode.used_at.is_(None),
        )
    )
    if found is None:
        return BAD_CODE
    found.used_at = _now()
    await audit.record(session, "auth.recovery_code_used", actor_id=user.id, actor_role=user.role)
    return None


async def _login(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    email: str,
    password: str,
    code: str | None = None,
) -> TokensOut:
    email = email.lower()
    ip = client_ip(request) or "unknown"
    if login_failures_by_account.blocked(email) or login_failures_by_ip.blocked(ip):
        login_failures_by_account.hit(email)
        login_failures_by_ip.hit(ip)
        retry = max(
            login_failures_by_account.retry_after(email), login_failures_by_ip.retry_after(ip)
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed logins. Try again later.",
            headers={"Retry-After": str(retry)},
        )

    user = await session.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        login_failures_by_account.hit(email)
        login_failures_by_ip.hit(ip)
        await audit.record(
            session,
            "auth.login_failed",
            actor_id=user.id if user else None,
            ip=ip,
            details={"email_sha256": email_fingerprint(email)},
        )
        await session.commit()  # keep the audit entry even though we answer 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    problem = await _second_factor(session, settings, user, code)
    if problem == NEED_CODE:  # the password was right: now ask for the code
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, NEED_CODE, headers={"X-Two-Factor": "required"}
        )
    if problem is not None:
        login_failures_by_account.hit(email)
        login_failures_by_ip.hit(ip)
        details = {"email_sha256": email_fingerprint(email), "reason": "two-factor code"}
        await audit.record(session, "auth.login_failed", actor_id=user.id, ip=ip, details=details)
        await session.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, problem, headers={"X-Two-Factor": "required"}
        )

    login_failures_by_account.reset(email)
    if needs_rehash(user.password_hash):  # e.g. after we raise the Argon2 cost
        user.password_hash = hash_password(password)
    tokens = await issue_tokens(session, settings, user)
    await audit.record(session, "auth.login", actor_id=user.id, actor_role=user.role, ip=ip)
    return tokens


@router.post("/login", response_model=TokensOut, responses={401: {}, 429: {}})
async def login(body: LoginIn, request: Request, session: Session, settings: Config):
    """Email + password → a 15-minute access token and a 7-day refresh token."""
    return await _login(request, session, settings, body.email, body.password, body.code)


@router.post("/token", response_model=TokensOut, include_in_schema=True, responses={401: {}})
async def login_form(
    request: Request,
    session: Session,
    settings: Config,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """Same as /login, as an HTML form. Used by Swagger's **Authorize** button
    (type your email in the *username* box; with two-factor login on, put the
    6-digit code in the *client_secret* box)."""
    return await _login(
        request, session, settings, form.username, form.password, form.client_secret
    )


@router.post("/refresh", response_model=TokensOut, responses={401: {}})
async def refresh(body: RefreshIn, request: Request, session: Session, settings: Config):
    """Swap a refresh token for new tokens. Each refresh token works **once**."""
    record = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_opaque_token(body.refresh_token))
    )
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if record.revoked_at is not None:
        # An already-used token came back: someone may have stolen it. End the
        # whole session for both the thief and the real user (FR-69).
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == record.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await audit.record(
            session,
            "auth.refresh_reuse_detected",
            actor_id=record.user_id,
            ip=client_ip(request),
            details={"session": str(record.family_id)},
        )
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = await session.get(User, record.user_id)
    if record.expires_at <= _now() or user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    tokens = await issue_tokens(session, settings, user, family=record.family_id)
    new_record = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_opaque_token(tokens.refresh_token)
        )
    )
    record.revoked_at = _now()
    record.replaced_by = new_record.id if new_record else None
    return tokens


@router.post("/logout", status_code=204)
async def logout(session: Session, caller: Annotated[Caller, Depends(current_caller)]):
    """End this session: its refresh token stops working, and so do its access tokens."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == caller.session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await audit.record(
        session, "auth.logout", actor_id=caller.id, actor_role=caller.role, ip=caller.ip
    )


@router.post("/logout-all", status_code=204)
async def logout_all(session: Session, caller: Annotated[Caller, Depends(current_caller)]):
    """End every session of this account (all phones and browsers)."""
    await revoke_all_sessions(session, caller.id)
    await audit.record(
        session, "auth.logout_all", actor_id=caller.id, actor_role=caller.role, ip=caller.ip
    )


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


@router.post("/password/forgot", response_model=MessageOut, status_code=202)
async def forgot_password(body: ForgotIn, request: Request, session: Session, settings: Config):
    """Email a reset link. The answer is the same whether or not the account exists."""
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is not None and user.is_active and user.password_hash:
        token, token_hash = new_opaque_token()
        session.add(
            PasswordReset(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=_now() + timedelta(minutes=settings.reset_token_minutes),
            )
        )
        await audit.record(
            session, "auth.password_reset_requested", actor_id=user.id, ip=client_ip(request)
        )
        await send_email(
            user.email,
            "Reset your AquaPulse password",
            f"Hello {user.full_name},\n\n"
            f"Use this code to set a new password within "
            f"{settings.reset_token_minutes} minutes:\n\n"
            f"    {token}\n\n"
            f"Or open this link: {settings.web_app_url}/reset-password?token={token}\n\n"
            "If you did not ask for this, ignore this email; your password stays the same.\n",
        )
    return MessageOut(message="If that account exists, we sent an email with a reset code.")


@router.post("/password/reset", response_model=MessageOut, responses={400: {}, 422: {}})
async def reset_password(body: ResetIn, request: Request, session: Session):
    """Set a new password with the emailed code (works once, for 30 minutes)."""
    reset = await session.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == hash_opaque_token(body.token))
    )
    if reset is None or reset.used_at is not None or reset.expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset code is invalid or expired")
    user = await session.get(User, reset.user_id)
    assert user is not None
    problem = password_problem(body.new_password, user.email)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    user.password_hash = hash_password(body.new_password)
    reset.used_at = _now()
    await revoke_all_sessions(session, user.id)  # anyone using the old password is logged out
    await audit.record(session, "auth.password_reset", actor_id=user.id, ip=client_ip(request))
    return MessageOut(message="Password changed. Please log in.")


@router.post("/invitations/accept", response_model=TokensOut, responses={400: {}, 409: {}, 422: {}})
async def accept_invitation(
    body: AcceptInviteIn, request: Request, session: Session, settings: Config
):
    """A resident joins their home with the emailed invitation code (works once, 48 h).

    New residents choose a password here. Someone who already has an account
    (e.g. moving to another flat) types their existing password instead.
    """
    invite = await session.scalar(
        select(Invitation).where(Invitation.token_hash == hash_opaque_token(body.token))
    )
    if invite is None or invite.used_at is not None or invite.expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is invalid or expired")

    user = await session.scalar(select(User).where(User.email == invite.email))
    if user is None:
        problem = password_problem(body.password, invite.email)
        if problem:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
        user = User(
            email=invite.email,
            full_name=invite.full_name,
            role="resident",
            password_hash=hash_password(body.password),
        )
        session.add(user)
        await session.flush()
    elif user.role != "resident" or not verify_password(user.password_hash, body.password):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account with this email already exists: type its current password",
        )

    invite.used_at = _now()
    session.add(
        Occupancy(
            home_id=invite.home_id,
            user_id=user.id,
            move_in=invite.move_in,
            is_billing_contact=invite.is_billing_contact,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This home already has a billing contact"
        ) from None
    home = await session.get(Home, invite.home_id)
    await audit.record(
        session,
        "resident.invitation_accepted",
        actor_id=user.id,
        actor_role="resident",
        ip=client_ip(request),
        target_type="home",
        target_id=invite.home_id,
        building_id=home.building_id if home else None,
        details={"invitation": str(invite.id)},
    )
    return await issue_tokens(session, settings, user)
