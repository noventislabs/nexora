"""Session authentication.

Design:

* Passwords are Argon2id hashes.
* The session credential is a random 256-bit token delivered in an ``HttpOnly``,
  ``SameSite=Lax`` cookie. Only an HMAC fingerprint reaches the database.
* State-changing requests additionally require a CSRF token echoed in a header — the
  double-submit pattern — which the browser cannot forge cross-origin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.crypto import constant_time_equals, new_token, token_fingerprint
from nexora.core.errors import AuthenticationRequired, Conflict, PermissionDenied, ValidationError
from nexora.core.passwords import WeakPassword, hash_password, needs_rehash, verify_password
from nexora.db.models import AuthSession, User
from nexora.db.models.enums import UserRole

SESSION_COOKIE = "nexora_session"
CSRF_COOKIE = "nexora_csrf"
CSRF_HEADER = "x-nexora-csrf"
SESSION_TTL = timedelta(days=14)
SESSION_IDLE_TIMEOUT = timedelta(days=7)


@dataclass(frozen=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: datetime
    user: User


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@") or " " in value:
        raise ValidationError("A valid email address is required.")
    if len(value) > 320:
        raise ValidationError("Email address is too long.")
    return value


def register_user(
    session: Session, *, email: str, password: str, display_name: str, role: UserRole = UserRole.OWNER
) -> User:
    normalized = normalize_email(email)
    existing = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if existing is not None:
        raise Conflict("An account with that email address already exists.")
    try:
        password_hash = hash_password(password)
    except WeakPassword as exc:
        raise ValidationError(str(exc)) from exc
    name = (display_name or "").strip()
    if not name:
        raise ValidationError("Display name is required.")
    user = User(
        email=normalized,
        password_hash=password_hash,
        display_name=name[:120],
        role=role.value,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, *, email: str, password: str) -> User:
    try:
        normalized = normalize_email(email)
    except ValidationError:
        # Do not distinguish "malformed" from "wrong" at the auth boundary.
        raise AuthenticationRequired("Email or password is incorrect.") from None
    user = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, password):
        raise AuthenticationRequired("Email or password is incorrect.")
    if not user.is_active:
        raise PermissionDenied("This account is disabled.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user


def issue_session(
    session: Session, user: User, *, user_agent: str | None = None, ip_address: str | None = None
) -> IssuedSession:
    session_token = new_token(32)
    csrf_token = new_token(32)
    now = datetime.now(UTC)
    expires_at = now + SESSION_TTL
    session.add(
        AuthSession(
            user_id=user.id,
            token_fingerprint=token_fingerprint(session_token),
            csrf_fingerprint=token_fingerprint(csrf_token),
            user_agent=(user_agent or "")[:512] or None,
            ip_address=(ip_address or "")[:64] or None,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
    )
    session.flush()
    return IssuedSession(
        session_token=session_token, csrf_token=csrf_token, expires_at=expires_at, user=user
    )


def resolve_session(session: Session, session_token: str | None) -> tuple[User, AuthSession]:
    if not session_token:
        raise AuthenticationRequired("Sign in to continue.")
    record = session.execute(
        select(AuthSession).where(AuthSession.token_fingerprint == token_fingerprint(session_token))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if record is None or record.revoked_at is not None:
        raise AuthenticationRequired("Your session is no longer valid. Sign in again.")
    if record.expires_at <= now:
        raise AuthenticationRequired("Your session has expired. Sign in again.")
    if record.last_seen_at + SESSION_IDLE_TIMEOUT <= now:
        record.revoked_at = now
        session.flush()
        raise AuthenticationRequired("Your session expired through inactivity. Sign in again.")

    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AuthenticationRequired("Your session is no longer valid. Sign in again.")

    record.last_seen_at = now
    session.flush()
    return user, record


def verify_csrf(record: AuthSession, csrf_token: str | None) -> None:
    if not csrf_token:
        raise PermissionDenied(
            f"Missing CSRF token. Send the '{CSRF_HEADER}' header on state-changing requests."
        )
    if not constant_time_equals(record.csrf_fingerprint, token_fingerprint(csrf_token)):
        raise PermissionDenied("CSRF token does not match this session.")


def revoke_session(session: Session, record: AuthSession) -> None:
    record.revoked_at = datetime.now(UTC)
    session.flush()


def revoke_all_sessions(session: Session, user_id: uuid.UUID) -> int:
    now = datetime.now(UTC)
    records = list(
        session.execute(
            select(AuthSession).where(
                AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
            )
        ).scalars()
    )
    for record in records:
        record.revoked_at = now
    session.flush()
    return len(records)


def purge_expired_sessions(session: Session) -> int:
    now = datetime.now(UTC)
    expired = list(
        session.execute(select(AuthSession).where(AuthSession.expires_at < now)).scalars()
    )
    for record in expired:
        session.delete(record)
    session.flush()
    return len(expired)
