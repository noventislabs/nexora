"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from nexora.api.deps import AuthUser, DbSession, RequestContext
from nexora.api.schemas import LoginRequest, RegisterRequest, SessionResponse, UserResponse
from nexora.config import settings
from nexora.core.errors import Conflict
from nexora.db.models import User
from nexora.services import audit
from nexora.services import auth as auth_service
from nexora.services.auth import CSRF_COOKIE, SESSION_COOKIE, IssuedSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        onboarding_completed=user.onboarding_completed_at is not None,
    )


def _set_session_cookies(response: Response, issued: IssuedSession) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "expires": issued.expires_at,
    }
    # The session credential is HttpOnly: JavaScript can never read it.
    response.set_cookie(SESSION_COOKIE, issued.session_token, httponly=True, **common)
    # The CSRF token must be readable by the frontend to echo it back in a header.
    response.set_cookie(CSRF_COOKIE, issued.csrf_token, httponly=False, **common)


def _clear_session_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.cookie_secure, samesite="lax")


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest, request: Request, response: Response, db: DbSession, ctx: RequestContext
) -> SessionResponse:
    """Create the first operator account.

    Open registration is limited to the first account; afterwards an existing owner
    provisions users. This keeps a self-hosted deployment from being claimed by a
    stranger who finds the port open.
    """
    from sqlalchemy import func, select

    user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    if user_count > 0:
        raise Conflict(
            "Registration is closed: this deployment already has an account. "
            "Sign in, or have an owner create your user."
        )

    user = auth_service.register_user(
        db, email=payload.email, password=payload.password, display_name=payload.display_name
    )
    issued = auth_service.issue_session(db, user, **ctx)
    audit.record(
        db, action="user.registered", user_id=user.id, entity_type="user", entity_id=user.id, **ctx
    )
    _set_session_cookies(response, issued)
    return SessionResponse(
        user=_user_response(user), csrf_token=issued.csrf_token, expires_at=issued.expires_at.isoformat()
    )


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession, ctx: RequestContext
) -> SessionResponse:
    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    issued = auth_service.issue_session(db, user, **ctx)
    audit.record(db, action="user.login", user_id=user.id, entity_type="user", entity_id=user.id, **ctx)
    _set_session_cookies(response, issued)
    return SessionResponse(
        user=_user_response(user), csrf_token=issued.csrf_token, expires_at=issued.expires_at.isoformat()
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbSession, current: AuthUser, ctx: RequestContext) -> Response:
    auth_service.revoke_session(db, current.session_record)
    audit.record(db, action="user.logout", user_id=current.user.id, **ctx)
    _clear_session_cookies(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def me(current: AuthUser) -> UserResponse:
    return _user_response(current.user)


@router.get("/registration-open")
def registration_open(db: DbSession) -> dict[str, bool]:
    """Lets the sign-in page show the right form without leaking anything else."""
    from sqlalchemy import func, select

    return {"open": db.execute(select(func.count()).select_from(User)).scalar_one() == 0}
