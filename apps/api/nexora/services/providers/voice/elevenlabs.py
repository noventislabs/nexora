"""ElevenLabs text-to-speech adapter.

Uses the ``with-timestamps`` endpoint, which returns character-level alignment
alongside the audio. Those are *measured* timings from the synthesizer, so subtitles
built from them are labelled ``timing_source="provider"`` rather than estimated.
"""

from __future__ import annotations

import base64
from typing import Any

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable, ValidationError
from nexora.services.http import http_client, raise_for_upstream, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.voice.base import (
    GeneratedAudio,
    Voice,
    VoiceProvider,
    voice_registry,
)

API_ROOT = "https://api.elevenlabs.io/v1"

#: The API's own per-request ceiling for the multilingual models.
MAX_INPUT_CHARS = 5000


@voice_registry.register
class ElevenLabsVoiceProvider(VoiceProvider):
    name = "elevenlabs"

    #: Character-level alignment comes back with the audio.
    provides_timings = True

    def availability(self) -> Availability:
        missing = []
        if not settings.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        if missing:
            return Availability.not_configured(
                provider=self.name,
                missing=tuple(missing),
                detail=(
                    "VOICE PROVIDER NOT CONFIGURED. Set ELEVENLABS_API_KEY, and "
                    "ELEVENLABS_VOICE_ID to pick a default voice."
                ),
            )
        return Availability.available(
            provider=self.name,
            detail=f"ElevenLabs model {settings.elevenlabs_model}",
            model=settings.elevenlabs_model,
            provides_timings=True,
        )

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": settings.elevenlabs_api_key, "accept": "application/json"}

    def list_voices(self) -> list[Voice]:
        """The voices this *account* actually has, not a hardcoded list."""
        self.require()
        with http_client(headers=self._headers()) as client:
            payload = request_json(client, "GET", f"{API_ROOT}/voices", provider="ElevenLabs")

        voices = []
        for item in payload.get("voices", []):
            if not isinstance(item, dict) or not item.get("voice_id"):
                continue
            labels = item.get("labels") or {}
            voices.append(
                Voice(
                    id=str(item["voice_id"]),
                    name=str(item.get("name") or item["voice_id"]),
                    languages=tuple(
                        str(code) for code in (item.get("verified_languages") or []) if code
                    ),
                    gender=labels.get("gender"),
                    preview_url=item.get("preview_url"),
                    metadata={"category": item.get("category"), "labels": labels},
                )
            )
        return voices

    def get_status(self) -> dict[str, Any]:
        """Real subscription quota, straight from the API."""
        availability = self.availability()
        if not availability.configured:
            return {"provider": self.name, "status": availability.status.value, "quota": None}

        with http_client(headers=self._headers()) as client:
            payload = request_json(
                client, "GET", f"{API_ROOT}/user/subscription", provider="ElevenLabs"
            )
        used = payload.get("character_count")
        limit = payload.get("character_limit")
        return {
            "provider": self.name,
            "status": availability.status.value,
            "model": settings.elevenlabs_model,
            "quota": {
                "characters_used": used,
                "character_limit": limit,
                "characters_remaining": (
                    limit - used if isinstance(used, int) and isinstance(limit, int) else None
                ),
                "resets_at_unix": payload.get("next_character_count_reset_unix"),
                "tier": payload.get("tier"),
            },
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
                f"ElevenLabs accepts at most {MAX_INPUT_CHARS} characters per request; "
                f"this request had {len(body_text)}."
            )

        resolved_voice = voice_id or settings.elevenlabs_voice_id
        if not resolved_voice:
            raise ValidationError(
                "No ElevenLabs voice was selected. Set ELEVENLABS_VOICE_ID, or choose a "
                "voice for this channel in settings."
            )

        with http_client(
            timeout=180.0, headers={**self._headers(), "content-type": "application/json"}
        ) as client:
            response = client.post(
                f"{API_ROOT}/text-to-speech/{resolved_voice}/with-timestamps",
                json={
                    "text": body_text,
                    "model_id": settings.elevenlabs_model,
                    "output_format": "mp3_44100_128",
                },
            )
            raise_for_upstream(response, provider="ElevenLabs")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderUnavailable("ElevenLabs returned a non-JSON response.") from exc

        encoded = payload.get("audio_base64")
        if not encoded:
            raise ProviderUnavailable("ElevenLabs returned no audio.")
        try:
            audio = base64.b64decode(encoded)
        except Exception as exc:
            raise ProviderUnavailable("ElevenLabs returned audio that could not be decoded.") from exc

        return GeneratedAudio(
            audio=audio,
            mime_type="audio/mpeg",
            provider=self.name,
            voice_id=str(resolved_voice),
            character_count=len(body_text),
            file_extension="mp3",
            alignment=_normalize_alignment(payload.get("alignment")),
        )


def _normalize_alignment(raw: Any) -> list[dict[str, Any]] | None:
    """Turn the API's parallel arrays into per-character records.

    Returns ``None`` rather than a guess if the shape is not what we expect, so a
    change upstream degrades to estimated timing instead of producing wrong subtitles.
    """
    if not isinstance(raw, dict):
        return None
    characters = raw.get("characters")
    starts = raw.get("character_start_times_seconds")
    ends = raw.get("character_end_times_seconds")
    if not (isinstance(characters, list) and isinstance(starts, list) and isinstance(ends, list)):
        return None
    if not characters or not (len(characters) == len(starts) == len(ends)):
        return None

    records = []
    for character, start, end in zip(characters, starts, ends, strict=True):
        try:
            records.append(
                {"character": str(character), "start": float(start), "end": float(end)}
            )
        except (TypeError, ValueError):
            return None
    return records
