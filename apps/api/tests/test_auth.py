"""Authentication, session and CSRF behaviour."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.crypto import token_fingerprint
from nexora.core.passwords import hash_password, verify_password
from nexora.db.models import AuthSession, User
from nexora.services import auth as auth_service
from tests.conftest import TEST_PASSWORD


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password(TEST_PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert TEST_PASSWORD not in hashed
    assert verify_password(hashed, TEST_PASSWORD) is True
    assert verify_password(hashed, TEST_PASSWORD + "!") is False


def test_short_password_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "a@b.test", "password": "short", "display_name": "A"},
    )
    assert response.status_code == 422


def test_register_first_user_then_closes_registration(client: TestClient, db: Session) -> None:
    assert client.get("/api/auth/registration-open").json() == {"open": True}

    response = client.post(
        "/api/auth/register",
        json={"email": "First@Nexora.test", "password": TEST_PASSWORD, "display_name": "First"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "first@nexora.test"  # normalized
    assert body["csrf_token"]

    assert client.get("/api/auth/registration-open").json() == {"open": False}
    second = client.post(
        "/api/auth/register",
        json={"email": "second@nexora.test", "password": TEST_PASSWORD, "display_name": "Second"},
    )
    assert second.status_code == 409


def test_session_token_is_never_stored_in_plaintext(client: TestClient, user: User, db: Session) -> None:
    response = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert response.status_code == 200
    raw_token = response.cookies.get(auth_service.SESSION_COOKIE) or client.cookies.get(
        auth_service.SESSION_COOKIE
    )
    assert raw_token

    record = db.execute(select(AuthSession)).scalars().one()
    assert record.token_fingerprint != raw_token
    assert record.token_fingerprint == token_fingerprint(raw_token)
    # The raw token appears nowhere in the row.
    assert raw_token not in str(record.__dict__)


def test_session_cookie_is_httponly(client: TestClient, user: User) -> None:
    response = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    set_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookies if c.startswith(auth_service.SESSION_COOKIE))
    csrf_cookie = next(c for c in set_cookies if c.startswith(auth_service.CSRF_COOKIE))
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie.replace("samesite", "SameSite")
    # The CSRF token must be readable by JS to be echoed back in a header.
    assert "HttpOnly" not in csrf_cookie


def test_wrong_password_is_rejected(client: TestClient, user: User) -> None:
    response = client.post("/api/auth/login", json={"email": user.email, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_unknown_email_gives_same_error_as_wrong_password(client: TestClient, user: User) -> None:
    unknown = client.post("/api/auth/login", json={"email": "nobody@nexora.test", "password": "x" * 20})
    wrong = client.post("/api/auth/login", json={"email": user.email, "password": "x" * 20})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["message"] == wrong.json()["message"]


def test_me_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_current_user(auth_client: TestClient, user: User) -> None:
    body = auth_client.get("/api/auth/me").json()
    assert body["email"] == user.email
    assert body["role"] == "owner"


def test_state_changing_request_requires_csrf_header(auth_client: TestClient) -> None:
    csrf = auth_client.headers.pop(auth_service.CSRF_HEADER)
    response = auth_client.post("/api/channels", json={"name": "No CSRF"})
    assert response.status_code == 403
    assert "CSRF" in response.json()["message"]

    auth_client.headers[auth_service.CSRF_HEADER] = csrf
    assert auth_client.post("/api/channels", json={"name": "With CSRF"}).status_code == 201


def test_wrong_csrf_token_rejected(auth_client: TestClient) -> None:
    auth_client.headers[auth_service.CSRF_HEADER] = "not-the-right-token"
    response = auth_client.post("/api/channels", json={"name": "Bad CSRF"})
    assert response.status_code == 403


def test_logout_revokes_session(auth_client: TestClient) -> None:
    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(client: TestClient, user: User, db: Session) -> None:
    from datetime import UTC, datetime, timedelta

    issued = auth_service.issue_session(db, user)
    db.commit()
    record = db.execute(select(AuthSession)).scalars().one()
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    client.cookies.set(auth_service.SESSION_COOKIE, issued.session_token)
    assert client.get("/api/auth/me").status_code == 401


def test_revoke_all_sessions(db: Session, user: User) -> None:
    auth_service.issue_session(db, user)
    auth_service.issue_session(db, user)
    assert auth_service.revoke_all_sessions(db, user.id) == 2
    with pytest.raises(Exception):
        auth_service.resolve_session(db, "nonexistent-token")
