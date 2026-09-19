"""Configuration-level availability for every external integration.

This module answers one question per integration: *can we do the real thing?* The
answer is derived from settings and, for YouTube, from the stored OAuth connection.
It is never derived from a hardcoded assumption.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexora.config import settings
from nexora.core.crypto import encryption_available
from nexora.db.models.enums import ComponentStatus
from nexora.services.providers.base import Availability

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session


def llm_availability() -> Availability:
    provider = (settings.llm_provider or "").strip().lower()
    if not provider:
        return Availability.not_configured(
            missing=("LLM_PROVIDER",),
            detail=(
                "Set LLM_PROVIDER to 'anthropic' or 'openai' and supply the matching API key. "
                "Topic, research, script, fact-check and metadata generation stay disabled until then."
            ),
        )
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            return Availability.not_configured(provider=provider, missing=("ANTHROPIC_API_KEY",))
        return Availability.available(
            provider=provider, detail=f"Anthropic model {settings.anthropic_model}",
            model=settings.anthropic_model,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            return Availability.not_configured(provider=provider, missing=("OPENAI_API_KEY",))
        return Availability.available(
            provider=provider, detail=f"OpenAI model {settings.openai_model}",
            model=settings.openai_model,
        )
    return Availability.unavailable(
        provider=provider,
        detail=f"Unknown LLM_PROVIDER '{provider}'. Supported values: anthropic, openai.",
    )


def voice_availability() -> Availability:
    provider = (settings.voice_provider or "").strip().lower()
    if not provider:
        return Availability.not_configured(
            missing=("VOICE_PROVIDER",),
            detail=(
                "VOICE PROVIDER NOT CONFIGURED. Set VOICE_PROVIDER to 'openai' or 'elevenlabs' "
                "and supply the matching API key."
            ),
        )
    if provider == "openai":
        if not settings.openai_api_key:
            return Availability.not_configured(provider=provider, missing=("OPENAI_API_KEY",))
        return Availability.available(
            provider=provider, detail=f"OpenAI TTS model {settings.openai_tts_model}",
            model=settings.openai_tts_model,
        )
    if provider == "elevenlabs":
        missing = []
        if not settings.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        if missing:
            return Availability.not_configured(provider=provider, missing=tuple(missing))
        return Availability.available(
            provider=provider, detail=f"ElevenLabs model {settings.elevenlabs_model}",
            model=settings.elevenlabs_model,
        )
    return Availability.unavailable(
        provider=provider,
        detail=f"Unknown VOICE_PROVIDER '{provider}'. Supported values: openai, elevenlabs.",
    )


def youtube_oauth_availability() -> Availability:
    """Whether the OAuth *application* is configured (not whether a channel is linked)."""
    missing = []
    if not settings.youtube_client_id:
        missing.append("YOUTUBE_CLIENT_ID")
    if not settings.youtube_client_secret:
        missing.append("YOUTUBE_CLIENT_SECRET")
    if missing:
        return Availability.not_configured(
            provider="youtube",
            missing=tuple(missing),
            detail=(
                "Create an OAuth 2.0 Web application client in Google Cloud Console with the "
                "YouTube Data API v3 enabled, then set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET."
            ),
        )
    if not encryption_available():
        return Availability.unavailable(
            provider="youtube",
            detail=(
                "ENCRYPTION_KEY is not set. OAuth tokens are only stored encrypted, so connecting "
                "a channel is blocked until a key is configured."
            ),
        )
    return Availability.available(
        provider="youtube",
        detail="OAuth client configured",
        redirect_uri=settings.youtube_redirect_uri,
    )


def youtube_data_api_availability() -> Availability:
    """Server API key used for public reads (trending videos, search)."""
    if not settings.youtube_api_key:
        return Availability.not_configured(
            provider="youtube_data_api",
            missing=("YOUTUBE_API_KEY",),
            detail=(
                "A server API key is required to read public YouTube trend data. "
                "OAuth-connected channel reads use the channel connection instead."
            ),
        )
    return Availability.available(provider="youtube_data_api", detail="Data API key configured")


def reddit_availability() -> Availability:
    missing = []
    if not settings.reddit_client_id:
        missing.append("REDDIT_CLIENT_ID")
    if not settings.reddit_client_secret:
        missing.append("REDDIT_CLIENT_SECRET")
    if missing:
        return Availability.not_configured(provider="reddit", missing=tuple(missing))
    return Availability.available(provider="reddit", detail="Reddit application credentials configured")


def storage_availability() -> Availability:
    from nexora.services.providers.storage import get_storage

    return get_storage().availability()


def encryption_availability() -> Availability:
    if not encryption_available():
        return Availability.not_configured(
            provider="fernet",
            missing=("ENCRYPTION_KEY",),
            detail=(
                "OAuth tokens cannot be stored without ENCRYPTION_KEY. Generate one with "
                '`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`.'
            ),
        )
    return Availability.available(provider="fernet", detail="Credential encryption key loaded")


def youtube_connection_availability(session: Session, channel_id) -> Availability:
    """Whether *this channel* has a live OAuth connection."""
    from nexora.db.models import YouTubeConnection
    from nexora.db.models.enums import ConnectionStatus

    connection = (
        session.query(YouTubeConnection).filter(YouTubeConnection.channel_id == channel_id).one_or_none()
    )
    if connection is None or connection.status == ConnectionStatus.NOT_CONNECTED.value:
        return Availability(
            status=ComponentStatus.NOT_CONNECTED,
            provider="youtube",
            detail="No YouTube channel is connected. Connect one from Channels → Connect YouTube.",
        )
    if connection.status != ConnectionStatus.CONNECTED.value:
        return Availability.unavailable(
            provider="youtube",
            detail=f"YouTube connection status is {connection.status}. {connection.last_error or ''}".strip(),
        )
    return Availability(
        status=ComponentStatus.CONNECTED,
        provider="youtube",
        detail=f"Connected to {connection.youtube_channel_title or connection.youtube_channel_id}",
        metadata={
            "youtube_channel_id": connection.youtube_channel_id,
            "has_analytics_scope": connection.has_analytics_scope,
            "has_monetary_scope": connection.has_monetary_scope,
        },
    )
