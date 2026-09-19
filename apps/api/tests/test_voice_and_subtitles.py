"""Voice synthesis and subtitle timing provenance."""

from __future__ import annotations

import httpx
import pytest
import respx

from nexora.config import settings
from nexora.core.errors import (
    ProviderNotConfigured,
    UpstreamPermanentError,
    ValidationError,
)
from nexora.services.providers.voice import get_voice, voice_registry
from nexora.services.providers.voice.elevenlabs import ElevenLabsVoiceProvider
from nexora.services.providers.voice.openai import OpenAIVoiceProvider
from nexora.services.video import subtitles as subtitle_service
from nexora.services.voice import split_for_provider
from tests.fixtures import feeds

OPENAI_URL = "https://api.openai.com/v1/audio/speech"
ELEVEN_ROOT = "https://api.elevenlabs.io/v1"


# -------------------------------------------------------------------- availability
def test_no_voice_provider_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_provider", "")
    with pytest.raises(ProviderNotConfigured) as exc:
        get_voice()
    assert "VOICE PROVIDER NOT CONFIGURED" in exc.value.message


def test_each_provider_declares_whether_it_returns_timings() -> None:
    assert voice_registry.names() == ["elevenlabs", "openai"]
    assert OpenAIVoiceProvider.provides_timings is False
    assert ElevenLabsVoiceProvider.provides_timings is True


def test_openai_without_a_key_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    availability = OpenAIVoiceProvider().availability()
    assert availability.status.value == "NOT CONFIGURED"
    assert availability.missing_settings == ("OPENAI_API_KEY",)


# -------------------------------------------------------------------------- OpenAI
@respx.mock
def test_openai_returns_audio_without_timings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, content=b"ID3\x04\x00" + b"\x00" * 64)
    )
    generated = OpenAIVoiceProvider().generate_audio("Hello there.", voice_id="nova")

    assert generated.mime_type == "audio/mpeg"
    assert generated.voice_id == "nova"
    assert generated.alignment is None, "this API returns no timings"
    body = route.calls[0].request
    assert body.headers["authorization"] == "Bearer fixture-key"


def test_openai_refuses_text_over_the_api_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    with pytest.raises(ValidationError) as exc:
        OpenAIVoiceProvider().generate_audio("x" * 5000)
    assert "chunking bug" in exc.value.message


@respx.mock
def test_openai_auth_failure_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "wrong")
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
    )
    with pytest.raises(UpstreamPermanentError):
        OpenAIVoiceProvider().generate_audio("Hello.")


# ---------------------------------------------------------------------- ElevenLabs
@respx.mock
def test_elevenlabs_returns_real_character_timings(monkeypatch) -> None:
    import base64

    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    text = "Hello there."
    respx.post(f"{ELEVEN_ROOT}/text-to-speech/fixture-voice-1/with-timestamps").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"ID3\x04\x00" + b"\x00" * 64).decode(),
                "alignment": {
                    "characters": list(text),
                    "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
                    "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(text))],
                },
            },
        )
    )
    generated = ElevenLabsVoiceProvider().generate_audio(text, voice_id="fixture-voice-1")

    assert generated.alignment is not None
    assert len(generated.alignment) == len(text)
    assert generated.alignment[0]["character"] == "H"


@respx.mock
def test_a_malformed_alignment_degrades_to_none_rather_than_guessing(monkeypatch) -> None:
    import base64

    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    respx.post(f"{ELEVEN_ROOT}/text-to-speech/v1/with-timestamps").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"ID3\x04\x00").decode(),
                # Mismatched lengths: unusable, and must not be half-read.
                "alignment": {
                    "characters": ["a", "b", "c"],
                    "character_start_times_seconds": [0.0, 0.1],
                    "character_end_times_seconds": [0.1, 0.2],
                },
            },
        )
    )
    generated = ElevenLabsVoiceProvider().generate_audio("abc", voice_id="v1")
    assert generated.alignment is None


def test_elevenlabs_without_a_voice_refuses_rather_than_picking_one(monkeypatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "")
    with pytest.raises(ValidationError) as exc:
        ElevenLabsVoiceProvider().generate_audio("Hello.")
    assert "No ElevenLabs voice was selected" in exc.value.message


@respx.mock
def test_elevenlabs_reports_real_quota(monkeypatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    respx.get(f"{ELEVEN_ROOT}/user/subscription").mock(
        return_value=httpx.Response(200, json=feeds.ELEVENLABS_SUBSCRIPTION)
    )
    status = ElevenLabsVoiceProvider().get_status()
    assert status["quota"]["characters_used"] == 12000
    assert status["quota"]["characters_remaining"] == 88000


@respx.mock
def test_elevenlabs_lists_the_accounts_own_voices(monkeypatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    respx.get(f"{ELEVEN_ROOT}/voices").mock(
        return_value=httpx.Response(200, json=feeds.ELEVENLABS_VOICES)
    )
    voices = ElevenLabsVoiceProvider().list_voices()
    assert [voice.id for voice in voices] == ["fixture-voice-1"]


def test_openai_status_admits_it_cannot_report_quota(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    status = OpenAIVoiceProvider().get_status()
    assert status["quota"] is None
    assert "does not expose a TTS quota endpoint" in status["quota_note"]


# ----------------------------------------------------------------------- chunking
def test_chunking_preserves_every_word() -> None:
    text = " ".join(f"Sentence number {i} with several words." for i in range(1, 80))
    chunks = split_for_provider(text, 600)
    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_a_single_overlong_sentence_is_split_on_words() -> None:
    sentence = " ".join(["word"] * 500) + "."
    chunks = split_for_provider(sentence, 400)
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert " ".join(chunks).split() == sentence.split()


# ---------------------------------------------------------------------- subtitles
def test_estimated_timing_is_labelled_and_spans_the_measured_duration() -> None:
    narration = "First sentence here. Second sentence follows. Third one closes it out."
    track = subtitle_service.build_track(narration, duration_seconds=12.0)

    assert track.timing_source == subtitle_service.ESTIMATED
    assert track.is_estimated is True
    assert "approximate" in track.note
    assert track.cues[0].start == 0.0
    assert track.cues[-1].end == 12.0
    # Cues never overlap or go backwards.
    for previous, following in zip(track.cues, track.cues[1:], strict=False):
        assert previous.end <= following.start + 1e-6


def test_provider_timings_are_used_when_available() -> None:
    narration = "First sentence here. Second sentence follows."
    alignment = [
        {"character": character, "start": index * 0.1, "end": (index + 1) * 0.1}
        for index, character in enumerate(narration)
    ]
    track = subtitle_service.build_track(
        narration, duration_seconds=len(narration) * 0.1, alignment=alignment
    )
    assert track.timing_source == subtitle_service.PROVIDER_TIMED
    assert track.is_estimated is False
    assert "character-level alignment" in track.note


def test_unmappable_alignment_falls_back_to_estimation() -> None:
    """Rather than emitting subtitles that drift, fall back and say so."""
    alignment = [{"character": "z", "start": 0.0, "end": 0.1}]
    track = subtitle_service.build_track(
        "Completely different narration text here.", duration_seconds=5.0, alignment=alignment
    )
    assert track.timing_source == subtitle_service.ESTIMATED


def test_zero_duration_is_refused() -> None:
    with pytest.raises(ValueError):
        subtitle_service.build_track("Some text.", duration_seconds=0)


def test_srt_and_vtt_formats() -> None:
    track = subtitle_service.build_track("One. Two.", duration_seconds=4.0)
    srt = subtitle_service.to_srt(track)
    assert "00:00:00,000 --> " in srt
    assert srt.splitlines()[0] == "1"

    vtt = subtitle_service.to_vtt(track)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> " in vtt
    # The provenance note travels with the file itself.
    assert "NOTE" in vtt


def test_provider_timed_vtt_has_no_estimation_note() -> None:
    narration = "One sentence."
    alignment = [
        {"character": character, "start": index * 0.1, "end": (index + 1) * 0.1}
        for index, character in enumerate(narration)
    ]
    track = subtitle_service.build_track(narration, duration_seconds=1.3, alignment=alignment)
    assert "NOTE" not in subtitle_service.to_vtt(track)


def test_long_sentences_are_split_into_readable_cues() -> None:
    long_sentence = " ".join(["word"] * 80) + "."
    chunks = subtitle_service.split_into_cues(long_sentence)
    assert len(chunks) > 1
    assert all(len(chunk) <= subtitle_service.MAX_CUE_CHARS for chunk in chunks)


def test_timestamp_formatting() -> None:
    assert subtitle_service.format_timestamp(0, separator=",") == "00:00:00,000"
    assert subtitle_service.format_timestamp(3661.5, separator=",") == "01:01:01,500"
    assert subtitle_service.format_timestamp(-5, separator=".") == "00:00:00.000"
