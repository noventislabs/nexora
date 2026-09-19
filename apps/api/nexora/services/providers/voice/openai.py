"""OpenAI text-to-speech adapter.

The audio endpoint returns audio only — no word or character timings. Subtitle timing
for this provider is therefore *estimated* from the measured audio duration, and every
artefact it produces is labelled ``timing_source="estimated"`` so the UI can say so.
"""

from __future__ import annotations

from typing import Any

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable, ValidationError
from nexora.services.http import http_client, raise_for_upstream
from nexora.services.providers.base import Availability
from nexora.services.providers.voice.base import (
    GeneratedAudio,
    Voice,
    VoiceProvider,
    voice_registry,
)

API_URL = "https://api.openai.com/v1/audio/speech"

#: The voices the OpenAI TTS models expose. Fixed by the API, not account-specific.
VOICES = (
    ("alloy", "Alloy", "neutral"),
    ("ash", "Ash", "neutral"),
    ("ballad", "Ballad", "neutral"),
    ("coral", "Coral", "neutral"),
    ("echo", "Echo", "neutral"),
    ("fable", "Fable", "neutral"),
    ("nova", "Nova", "neutral"),
    ("onyx", "Onyx", "neutral"),
    ("sage", "Sage", "neutral"),
    ("shimmer", "Shimmer", "neutral"),
)

#: The endpoint rejects longer input outright, so this is checked before sending.
MAX_INPUT_CHARS = 4096


@voice_registry.register
class OpenAIVoiceProvider(VoiceProvider):
    name = "openai"

    #: This API returns no timing data with the audio.
    provides_timings = False

    def availability(self) -> Availability:
        if not settings.openai_api_key:
            return Availability.not_configured(
                provider=self.name,
                missing=("OPENAI_API_KEY",),
                detail="VOICE PROVIDER NOT CONFIGURED. Set OPENAI_API_KEY to enable OpenAI TTS.",
            )
        return Availability.available(
            provider=self.name,
            detail=f"OpenAI TTS model {settings.openai_tts_model}",
            model=settings.openai_tts_model,
            provides_timings=False,
        )

    def list_voices(self) -> list[Voice]:
        self.require()
        return [
            Voice(id=voice_id, name=name, languages=("multi",), gender=gender)
            for voice_id, name, gender in VOICES
        ]

    def get_status(self) -> dict[str, Any]:
        """OpenAI exposes no quota endpoint for TTS, so this reports configuration only."""
        availability = self.availability()
        return {
            "provider": self.name,
            "status": availability.status.value,
            "model": settings.openai_tts_model,
            "quota": None,
            "quota_note": "The OpenAI API does not expose a TTS quota endpoint.",
        }

    def generate_audio(
        self, text: str, *, voice_id: str | None = None, language: str = "en"
    ) -> GeneratedAudio:
        self.require()
        body_text = (text or "").strip()
        if not body_text:
            raise ValidationError("There is no narration text to synthesize.")
        if len(body_text) > MAX_INPUT_CHARS:
            raise ValidationError(
                f"OpenAI TTS accepts at most {MAX_INPUT_CHARS} characters per request; "
                f"this request had {len(body_text)}. Narration is split into chunks before "
                "it reaches the provider, so this indicates a chunking bug."
            )

        resolved_voice = voice_id or settings.openai_tts_voice or "alloy"
        with http_client(
            timeout=180.0,
            headers={
                "authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            },
        ) as client:
            response = client.post(
                API_URL,
                json={
                    "model": settings.openai_tts_model,
                    "voice": resolved_voice,
                    "input": body_text,
                    "response_format": "mp3",
                },
            )
            raise_for_upstream(response, provider="OpenAI TTS")
            audio = response.content

        if not audio:
            raise ProviderUnavailable("OpenAI TTS returned an empty audio body.")
        return GeneratedAudio(
            audio=audio,
            mime_type="audio/mpeg",
            provider=self.name,
            voice_id=resolved_voice,
            character_count=len(body_text),
            file_extension="mp3",
        )
