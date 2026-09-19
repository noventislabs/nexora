"""FastAPI dependencies: database session, authenticated user, CSRF, channel access."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from nexora.core.errors import PermissionDenied
from nexora.db.models import AuthSession, Channel, User
from nexora.db.session import get_session_factory
from nexora.services import auth as auth_service
from nexora.services import channels as channel_service

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db(request: Request) -> Iterator[Session]:
    """One transaction per request, committed on a successful response."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


@dataclass
class CurrentUser:
    user: User
    session_record: AuthSession

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(request: Request, db: DbSession) -> CurrentUser:
    """Resolve the session cookie and enforce CSRF on state-changing methods."""
    token = request.cookies.get(auth_service.SESSION_COOKIE)
    user, record = auth_service.resolve_session(db, token)
    if request.method not in SAFE_METHODS:
        auth_service.verify_csrf(record, request.headers.get(auth_service.CSRF_HEADER))
    request.state.user_id = str(user.id)
    return CurrentUser(user=user, session_record=record)


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]


def require_writer(current: AuthUser) -> CurrentUser:
    """Block read-only accounts from mutating endpoints."""
    if current.user.role == "viewer":
        raise PermissionDenied("This account has read-only access.")
    return current


Writer = Annotated[CurrentUser, Depends(require_writer)]


def get_channel(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> Channel:
    return channel_service.get_channel_for_user(db, current.user, channel_id)


ChannelDep = Annotated[Channel, Depends(get_channel)]


def request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


RequestContext = Annotated[dict, Depends(request_context)]
