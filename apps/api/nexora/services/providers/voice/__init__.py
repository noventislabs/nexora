"""Voice providers. Importing this package registers every adapter."""

from nexora.config import settings
from nexora.core.errors import ProviderNotConfigured
from nexora.services.providers.voice import elevenlabs as _elevenlabs  # noqa: F401
from nexora.services.providers.voice import openai as _openai  # noqa: F401
from nexora.services.providers.voice.base import (
    GeneratedAudio,
    Voice,
    VoiceProvider,
    voice_registry,
)

__all__ = ["GeneratedAudio", "Voice", "VoiceProvider", "get_voice", "voice_registry"]


def get_voice() -> VoiceProvider:
    """The configured voice provider.

    Raises :class:`ProviderNotConfigured` when ``VOICE_PROVIDER`` is unset, so callers
    surface ``VOICE PROVIDER NOT CONFIGURED`` rather than producing silence or a
    placeholder track.
    """
    name = (settings.voice_provider or "").strip().lower()
    if not name:
        raise ProviderNotConfigured(
            "VOICE PROVIDER NOT CONFIGURED. Set VOICE_PROVIDER to 'openai' or "
            "'elevenlabs' and supply the matching API key.",
            details={"missing_settings": ["VOICE_PROVIDER"]},
        )
    provider = voice_registry.create(name)
    provider.require()
    return provider
