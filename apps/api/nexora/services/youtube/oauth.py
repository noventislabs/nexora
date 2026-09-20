"""YouTube OAuth connection lifecycle.

NEXORA never handles a YouTube password. The operator authenticates with Google, and
Google returns an authorization code that is exchanged for tokens.

Tokens are sealed with Fernet before they touch the database, and the plaintext is
never logged. The CSRF ``state`` and the PKCE verifier are single-use and expire in
ten minutes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.crypto import decrypt_str, encrypt_str, new_token, token_fingerprint
from nexora.core.errors import Conflict, NotFound, PermissionDenied, ProviderUnavailable
from nexora.core.logging import get_logger
from nexora.db.models import Channel, OAuthState, User, YouTubeConnection
from nexora.db.models.enums import ActorType, ConnectionStatus
from nexora.services import audit
from nexora.services.providers.youtube import get_youtube
from nexora.services.providers.youtube.base import OAuthTokens
from nexora.services.providers.youtube.google import (
    ANALYTICS_SCOPE,
    MONETARY_SCOPE,
    generate_pkce_verifier,
    pkce_challenge,
)

logger = get_logger(__name__)

STATE_TTL = timedelta(minutes=10)
#: Refresh this far ahead of expiry so a long upload never dies mid-flight.
REFRESH_MARGIN = timedelta(minutes=5)


@dataclass
class AuthorizationRequest:
    authorization_url: str
    state: str
    expires_at: datetime


def start_authorization(
    session: Session,
    user: User,
    channel: Channel,
    *,
    include_analytics: bool = True,
    include_monetary: bool = False,
    redirect_to: str | None = None,
) -> AuthorizationRequest:
    """Begin the OAuth flow, storing single-use CSRF state and a PKCE verifier."""
    provider = get_youtube(
        include_analytics=include_analytics, include_monetary=include_monetary
    )

    state = new_token(32)
    verifier = generate_pkce_verifier()
    now = datetime.now(UTC)

    session.add(
        OAuthState(
            state_fingerprint=token_fingerprint(state),
            user_id=user.id,
            channel_id=channel.id,
            # The verifier is a credential for the exchange, so it is stored sealed.
            code_verifier_encrypted=encrypt_str(verifier),
            redirect_to=redirect_to,
            created_at=now,
            expires_at=now + STATE_TTL,
        )
    )
    session.flush()

    url = provider.authorization_url(state=state, code_challenge=pkce_challenge(verifier))
    logger.info(
        "youtube.oauth_started",
        extra={"channel_id": str(channel.id), "scopes": list(provider.scopes)},
    )
    return AuthorizationRequest(
        authorization_url=url, state=state, expires_at=now + STATE_TTL
    )


def complete_authorization(
    session: Session, *, state: str, code: str
) -> tuple[Channel, YouTubeConnection]:
    """Exchange the authorization code and store the sealed tokens."""
    record = session.execute(
        select(OAuthState).where(OAuthState.state_fingerprint == token_fingerprint(state))
    ).scalar_one_or_none()
    now = datetime.now(UTC)

    if record is None:
        raise PermissionDenied("This authorization response does not match any pending request.")
    if record.consumed_at is not None:
        raise Conflict("This authorization response has already been used.")
    if record.expires_at <= now:
        raise PermissionDenied("The authorization request expired. Start the connection again.")

    # Single use, marked before the exchange so a replay cannot race it.
    record.consumed_at = now
    session.flush()

    channel = session.get(Channel, record.channel_id)
    if channel is None:
        raise NotFound("The channel this authorization was started for no longer exists.")

    verifier = decrypt_str(record.code_verifier_encrypted or "")
    provider = get_youtube()
    tokens = provider.exchange_code(code=code, code_verifier=verifier)
    remote = provider.get_my_channel(tokens.access_token)

    connection = session.execute(
        select(YouTubeConnection).where(YouTubeConnection.channel_id == channel.id)
    ).scalar_one_or_none()
    if connection is None:
        connection = YouTubeConnection(channel_id=channel.id)
        session.add(connection)

    _store_tokens(connection, tokens)
    connection.youtube_channel_id = remote.id
    connection.youtube_channel_title = remote.title
    connection.youtube_custom_url = remote.custom_url
    connection.status = ConnectionStatus.CONNECTED.value
    connection.connected_at = now
    connection.last_error = None
    session.flush()

    audit.record(
        session,
        action="youtube.connected",
        actor_type=ActorType.USER,
        user_id=record.user_id,
        channel_id=channel.id,
        entity_type="youtube_connection",
        entity_id=connection.id,
        summary=(
            f"Connected YouTube channel {remote.id} ('{remote.title}') with scopes: "
            f"{', '.join(tokens.scopes)}."
        ),
    )
    logger.info(
        "youtube.connected",
        extra={
            "channel_id": str(channel.id),
            "youtube_channel_id": remote.id,
            "has_analytics_scope": connection.has_analytics_scope,
            "has_monetary_scope": connection.has_monetary_scope,
        },
    )
    return channel, connection


def _store_tokens(connection: YouTubeConnection, tokens: OAuthTokens) -> None:
    """Seal tokens before they reach the database. Plaintext never persists."""
    connection.access_token_encrypted = encrypt_str(tokens.access_token)
    if tokens.refresh_token:
        connection.refresh_token_encrypted = encrypt_str(tokens.refresh_token)
    connection.token_expires_at = tokens.expires_at
    connection.scopes = list(tokens.scopes)
    connection.has_analytics_scope = ANALYTICS_SCOPE in tokens.scopes
    connection.has_monetary_scope = MONETARY_SCOPE in tokens.scopes
    connection.last_refreshed_at = datetime.now(UTC)


def get_connection(session: Session, channel_id: uuid.UUID) -> YouTubeConnection:
    connection = session.execute(
        select(YouTubeConnection).where(YouTubeConnection.channel_id == channel_id)
    ).scalar_one_or_none()
    if connection is None:
        raise NotFound("This channel has no YouTube connection record.")
    return connection


def require_connected(session: Session, channel_id: uuid.UUID) -> YouTubeConnection:
    connection = get_connection(session, channel_id)
    if connection.status != ConnectionStatus.CONNECTED.value:
        raise ProviderUnavailable(
            "No YouTube channel is connected. Connect one from Channels → Connect YouTube. "
            "A channel id alone does not grant upload access — Google requires consent.",
            details={"status": connection.status},
        )
    return connection


def access_token(session: Session, connection: YouTubeConnection) -> str:
    """Return a usable access token, refreshing ahead of expiry when needed."""
    if not connection.access_token_encrypted:
        raise ProviderUnavailable("This connection holds no access token. Reconnect the channel.")

    expires = connection.token_expires_at
    if expires is not None and expires - REFRESH_MARGIN > datetime.now(UTC):
        return decrypt_str(connection.access_token_encrypted)

    if not connection.refresh_token_encrypted:
        connection.status = ConnectionStatus.EXPIRED.value
        connection.last_error = "The access token expired and no refresh token is stored."
        session.flush()
        raise ProviderUnavailable(connection.last_error)

    provider = get_youtube()
    try:
        tokens = provider.refresh_tokens(decrypt_str(connection.refresh_token_encrypted))
    except Exception as exc:
        # Google revokes refresh tokens when the user withdraws consent.
        connection.status = ConnectionStatus.REVOKED.value
        connection.last_error = f"Token refresh failed: {exc}"
        session.flush()
        logger.warning(
            "youtube.refresh_failed",
            extra={"channel_id": str(connection.channel_id), "error": str(exc)},
        )
        raise ProviderUnavailable(
            "The YouTube connection is no longer valid — access may have been revoked in "
            "your Google account. Reconnect the channel."
        ) from exc

    _store_tokens(connection, tokens)
    connection.status = ConnectionStatus.CONNECTED.value
    connection.last_error = None
    session.flush()
    logger.info("youtube.token_refreshed", extra={"channel_id": str(connection.channel_id)})
    return tokens.access_token


def disconnect(
    session: Session, channel: Channel, *, user_id: uuid.UUID, revoke_remote: bool = True
) -> YouTubeConnection:
    """Disconnect a channel, revoking the grant with Google and clearing the tokens."""
    connection = get_connection(session, channel.id)
    revoked = False

    if revoke_remote and connection.refresh_token_encrypted:
        try:
            get_youtube().revoke(decrypt_str(connection.refresh_token_encrypted))
            revoked = True
        except Exception as exc:
            # Local disconnection must still happen even if Google is unreachable.
            logger.warning("youtube.revoke_failed", extra={"error": str(exc)})

    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_expires_at = None
    connection.status = ConnectionStatus.NOT_CONNECTED.value
    connection.scopes = []
    connection.has_analytics_scope = False
    connection.has_monetary_scope = False
    connection.connected_at = None
    connection.last_error = None
    session.flush()

    audit.record(
        session,
        action="youtube.disconnected",
        user_id=user_id,
        channel_id=channel.id,
        entity_type="youtube_connection",
        entity_id=connection.id,
        summary=(
            "Disconnected YouTube"
            + (" and revoked the grant with Google." if revoked else "; the remote grant could not be revoked.")
        ),
    )
    return connection


def purge_expired_states(session: Session) -> int:
    expired = list(
        session.execute(
            select(OAuthState).where(OAuthState.expires_at < datetime.now(UTC))
        ).scalars()
    )
    for record in expired:
        session.delete(record)
    session.flush()
    return len(expired)


def connection_to_dict(connection: YouTubeConnection) -> dict[str, Any]:
    """Serialize a connection. No token material is ever included."""
    return {
        "status": connection.status,
        "connected": connection.status == ConnectionStatus.CONNECTED.value,
        "youtube_channel_id": connection.youtube_channel_id,
        "youtube_channel_title": connection.youtube_channel_title,
        "youtube_custom_url": connection.youtube_custom_url,
        "scopes": connection.scopes or [],
        "has_analytics_scope": connection.has_analytics_scope,
        "has_monetary_scope": connection.has_monetary_scope,
        "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
        "last_refreshed_at": (
            connection.last_refreshed_at.isoformat() if connection.last_refreshed_at else None
        ),
        "token_expires_at": (
            connection.token_expires_at.isoformat() if connection.token_expires_at else None
        ),
        "last_error": connection.last_error,
        "public_channel_id": connection.public_channel_id,
        "public_channel_title": connection.public_channel_title,
        "public_verified_at": (
            connection.public_verified_at.isoformat() if connection.public_verified_at else None
        ),
        "capabilities": {
            "upload": connection.status == ConnectionStatus.CONNECTED.value,
            "channel_analytics": connection.has_analytics_scope,
            "revenue": connection.has_monetary_scope,
            "public_read": bool(connection.public_channel_id),
        },
    }
