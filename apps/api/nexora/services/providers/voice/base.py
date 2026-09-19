"""VoiceProvider interface for narration synthesis."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from nexora.services.providers.base import Provider, ProviderRegistry


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    languages: tuple[str, ...] = ()
    gender: str | None = None
    preview_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedAudio:
    audio: bytes
    mime_type: str
    provider: str
    voice_id: str
    character_count: int
    file_extension: str = "mp3"


class VoiceProvider(Provider, abc.ABC):
    kind = "voice"

    @abc.abstractmethod
    def list_voices(self) -> list[Voice]:
        """Return the voices this provider actually offers for the configured account."""

    @abc.abstractmethod
    def generate_audio(
        self, text: str, *, voice_id: str | None = None, language: str = "en"
    ) -> GeneratedAudio:
        """Synthesize narration. Must raise rather than return silence on failure."""

    @abc.abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Provider-reported status (quota, account state) where the API exposes it."""


voice_registry: ProviderRegistry[VoiceProvider] = ProviderRegistry("voice")
