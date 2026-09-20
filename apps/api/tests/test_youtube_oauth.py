"""YouTube OAuth: PKCE, single-use state, encrypted tokens, refresh and revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import (
    Conflict,
    PermissionDenied,
    ProviderNotConfigured,
    ProviderUnavailable,
)
from nexora.db.models import AuditLog, Channel, OAuthState, User, YouTubeConnection
from nexora.services.providers.youtube.google import GoogleYouTubeProvider
from nexora.services.youtube import oauth as oauth_service
from tests.fixtures import feeds

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "fixture-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "youtube_client_secret", "fixture-client-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(
        settings, "youtube_redirect_uri", "http://localhost:8000/api/youtube/oauth/callback"
    )


def test_without_oauth_credentials_the_provider_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_client_id", "")
    monkeypatch.setattr(settings, "youtube_client_secret", "")
    availability = GoogleYouTubeProvider().availability()
    assert availability.status.value == "NOT CONFIGURED"
    assert set(availability.missing_settings) == {"YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"}


def test_credentials_without_an_encryption_key_are_refused(monkeypatch) -> None:
    """Tokens are only ever stored encrypted, so no key means no connecting."""
    monkeypatch.setattr(settings, "youtube_client_id", "id")
    monkeypatch.setattr(settings, "youtube_client_secret", "secret")
    monkeypatch.setattr(settings, "encryption_key", "")
    availability = GoogleYouTubeProvider().availability()
    assert availability.status.value == "UNAVAILABLE"
    assert "ENCRYPTION_KEY" in availability.detail


def test_starting_a_connection_without_credentials_raises(
    db: Session, user: User, channel: Channel, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "youtube_client_id", "")
    with pytest.raises(ProviderNotConfigured):
        oauth_service.start_authorization(db, user, channel)
    assert db.query(OAuthState).count() == 0


def test_authorization_url_uses_pkce_and_requests_offline_access(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()

    query = parse_qs(urlparse(request.authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43  # base64url SHA-256, unpadded
    assert query["access_type"] == ["offline"], "a refresh token requires offline access"
    assert query["prompt"] == ["consent"]
    assert query["response_type"] == ["code"]
    scopes = query["scope"][0].split()
    assert "https://www.googleapis.com/auth/youtube.upload" in scopes
    assert "https://www.googleapis.com/auth/yt-analytics.readonly" in scopes
    # The monetary scope is a separate decision and is not requested by default.
    assert "https://www.googleapis.com/auth/yt-analytics-monetary.readonly" not in scopes


def test_the_state_is_stored_only_as_a_fingerprint(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()

    record = db.query(OAuthState).one()
    assert record.state_fingerprint != request.state
    assert request.state not in str(record.__dict__)
    # The PKCE verifier is a credential for the exchange, so it is sealed too.
    assert record.code_verifier_encrypted
    assert not record.code_verifier_encrypted.startswith("nexora")


@respx.mock
def test_completing_the_flow_stores_encrypted_tokens(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()

    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE)
    )
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL)
    )

    _, connection = oauth_service.complete_authorization(
        db, state=request.state, code="fixture-auth-code"
    )
    db.commit()

    # The PKCE verifier was sent back on the exchange.
    sent = dict(httpx.QueryParams(token_route.calls[0].request.content.decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"]

    assert connection.status == "connected"
    assert connection.youtube_channel_id == "UCFIXTURECHANNELID000000"
    assert connection.has_analytics_scope is True
    assert connection.has_monetary_scope is False

    # Neither token is readable from the row.
    assert connection.access_token_encrypted
    assert "fixture-access-token" not in connection.access_token_encrypted
    assert "fixture-refresh-token" not in connection.refresh_token_encrypted
    assert "fixture-access-token" not in str(connection.__dict__)


@respx.mock
def test_a_missing_refresh_token_is_refused(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    """Without a refresh token the connection would silently die in an hour."""
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    payload = {**feeds.GOOGLE_TOKEN_RESPONSE}
    payload.pop("refresh_token")
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderUnavailable) as exc:
        oauth_service.complete_authorization(db, state=request.state, code="code")
    assert "refresh token" in exc.value.message


@respx.mock
def test_state_is_single_use(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))

    oauth_service.complete_authorization(db, state=request.state, code="code")
    db.commit()

    with pytest.raises(Conflict):
        oauth_service.complete_authorization(db, state=request.state, code="code")


def test_an_unknown_state_is_rejected(db: Session, oauth_configured) -> None:
    with pytest.raises(PermissionDenied):
        oauth_service.complete_authorization(db, state="never-issued", code="code")


def test_an_expired_state_is_rejected(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    record = db.query(OAuthState).one()
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(PermissionDenied) as exc:
        oauth_service.complete_authorization(db, state=request.state, code="code")
    assert "expired" in exc.value.message


@respx.mock
def test_a_hidden_subscriber_count_is_absent_not_zero(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL_HIDDEN_SUBS)
    )
    oauth_service.complete_authorization(db, state=request.state, code="code")

    remote = GoogleYouTubeProvider().get_my_channel("fixture-access-token")
    assert remote.subscriber_count is None
    assert remote.subscriber_count_hidden is True
    assert remote.view_count == 98765


@respx.mock
def test_tokens_refresh_before_expiry(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    _, connection = oauth_service.complete_authorization(db, state=request.state, code="code")
    db.commit()

    # A token still comfortably valid is reused without a network call.
    respx.post(TOKEN_URL).mock(side_effect=AssertionError("must not refresh a fresh token"))
    assert oauth_service.access_token(db, connection) == "fixture-access-token"

    # Once it is inside the refresh margin, it is refreshed.
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    db.commit()
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=feeds.GOOGLE_REFRESH_RESPONSE)
    )
    assert oauth_service.access_token(db, connection) == "fixture-refreshed-token"
    assert connection.status == "connected"


@respx.mock
def test_a_revoked_grant_marks_the_connection_revoked(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    _, connection = oauth_service.complete_authorization(db, state=request.state, code="code")
    connection.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(ProviderUnavailable) as exc:
        oauth_service.access_token(db, connection)
    assert "reconnect" in exc.value.message.lower()
    assert connection.status == "revoked"


@respx.mock
def test_disconnect_revokes_and_clears_tokens(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    oauth_service.complete_authorization(db, state=request.state, code="code")
    db.commit()

    revoke = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    connection = oauth_service.disconnect(db, channel, user_id=user.id)
    db.commit()

    assert revoke.call_count == 1
    assert connection.status == "not_connected"
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None
    assert connection.scopes == []


@respx.mock
def test_disconnect_still_clears_locally_when_google_is_unreachable(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    oauth_service.complete_authorization(db, state=request.state, code="code")
    db.commit()

    respx.post(REVOKE_URL).mock(side_effect=httpx.ConnectError("offline"))
    connection = oauth_service.disconnect(db, channel, user_id=user.id)
    assert connection.status == "not_connected"
    assert connection.access_token_encrypted is None


@respx.mock
def test_the_connection_payload_never_leaks_tokens(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    _, connection = oauth_service.complete_authorization(db, state=request.state, code="code")

    payload = str(oauth_service.connection_to_dict(connection))
    assert "fixture-access-token" not in payload
    assert "fixture-refresh-token" not in payload
    assert "token_encrypted" not in payload


@respx.mock
def test_connecting_is_audited(
    db: Session, user: User, channel: Channel, oauth_configured
) -> None:
    request = oauth_service.start_authorization(db, user, channel)
    db.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=feeds.GOOGLE_TOKEN_RESPONSE))
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL))
    oauth_service.complete_authorization(db, state=request.state, code="code")
    db.commit()

    entry = db.query(AuditLog).filter(AuditLog.action == "youtube.connected").one()
    assert "UCFIXTURECHANNELID000000" in (entry.summary or "")
    assert "fixture-access-token" not in (entry.summary or "")


def test_expired_states_are_purged(db: Session, user: User, channel: Channel, oauth_configured) -> None:
    oauth_service.start_authorization(db, user, channel)
    db.commit()
    record = db.query(OAuthState).one()
    record.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    assert oauth_service.purge_expired_states(db) == 1
    assert db.query(OAuthState).count() == 0


def test_require_connected_refuses_without_consent(db: Session, channel: Channel) -> None:
    """A new channel already has a connection row, in the NOT_CONNECTED state."""
    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    assert connection.status == "not_connected"

    with pytest.raises(ProviderUnavailable) as exc:
        oauth_service.require_connected(db, channel.id)
    assert "channel id alone does not grant upload access" in exc.value.message
