"""Test fixtures.

These tests run against real PostgreSQL and real Redis — the same engines the
application uses in production. Nothing here stubs the database or the queue.
Only *outbound third-party HTTP* is intercepted (with ``respx``), and every such
fixture is explicitly labelled as a test fixture.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

# Environment must be set before any nexora module reads settings.
os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+psycopg://nexora:nexora@127.0.0.1:5432/nexora_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("APP_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("STORAGE_BACKEND", "local")

# Hermetic by construction: a developer's real .env must never change what the unit
# suite asserts, and no test may reach a third party by accident. Tests that need a
# credential set it explicitly with monkeypatch; the live integration tests read the
# real value through `tests.live.live_credential` instead.
for _credential in (
    "YOUTUBE_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "LLM_PROVIDER",
    "VOICE_PROVIDER",
):
    os.environ[_credential] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nexora.api.deps import get_db  # noqa: E402
from nexora.db.models import Base, User  # noqa: E402
from nexora.db.session import get_engine, get_session_factory  # noqa: E402
from nexora.main import create_app  # noqa: E402
from nexora.services import auth as auth_service  # noqa: E402
from nexora.services import channels as channel_service  # noqa: E402

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Build the schema from the real Alembic migration, not from metadata."""
    from alembic import command
    from alembic.config import Config

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    config = Config(str(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")))
    config.set_main_option("script_location", "nexora/migrations")
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    """Truncate between tests so every test starts from a known empty database."""
    engine = get_engine()
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables if name != "alembic_version")
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """TestClient sharing the test's session so assertions see request-made changes."""
    app = create_app()

    def _override_db() -> Iterator[Session]:
        # Mirror production semantics: each request runs in its own (nested)
        # transaction that is rolled back on error, while the outer test transaction
        # keeps fixture data alive.
        nested = db.begin_nested()
        try:
            yield db
            nested.commit()
            db.flush()
        except Exception:
            if nested.is_active:
                nested.rollback()
            raise

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(db: Session) -> User:
    return auth_service.register_user(
        db, email="operator@nexora.test", password=TEST_PASSWORD, display_name="Operator"
    )


@pytest.fixture
def auth_client(client: TestClient, user: User) -> TestClient:
    """A TestClient with a live session cookie and the CSRF header pre-set."""
    response = client.post(
        "/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[auth_service.CSRF_HEADER] = response.json()["csrf_token"]
    return client


@pytest.fixture
def channel(db: Session, user: User):
    channel = channel_service.create_channel(db, user)
    db.commit()
    return channel


@pytest.fixture
def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@nexora.test"
