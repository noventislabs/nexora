"""Health checks and capability reporting reflect reality, not assumptions."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nexora.config import settings
from nexora.services import availability as avail
from nexora.services.ffmpeg_runtime import FFmpegUnavailable, ffmpeg_info, require_ffmpeg


def test_health_probes_database_and_queue(client: TestClient) -> None:
    body = client.get("/api/system/health").json()
    components = {c["name"]: c for c in body["components"]}

    assert components["database"]["status"] == "HEALTHY"
    assert components["database"]["latency_ms"] is not None, "latency proves a real round trip"
    assert components["database"]["metadata"]["migration_revision"], "migrations must be applied"

    assert components["queue"]["status"] == "HEALTHY"
    assert components["queue"]["metadata"]["redis_version"]


def test_health_reports_unconfigured_providers_explicitly(client: TestClient) -> None:
    components = {c["name"]: c for c in client.get("/api/system/health").json()["components"]}
    for name in ("youtube", "llm_provider", "voice_provider"):
        assert components[name]["status"] == "NOT CONFIGURED"
        assert components[name]["metadata"]["missing_settings"], "must name what is missing"


def test_health_is_reachable_without_authentication(client: TestClient) -> None:
    assert client.get("/api/system/health").status_code == 200


def test_capabilities_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/system/capabilities").status_code == 401


def test_capabilities_lists_every_integration(auth_client: TestClient) -> None:
    body = auth_client.get("/api/system/capabilities").json()
    assert set(body) == {
        "llm", "voice", "youtube_oauth", "youtube_data_api", "reddit",
        "storage", "encryption", "ffmpeg",
    }
    assert body["llm"]["status"] == "NOT CONFIGURED"
    assert body["storage"]["status"] in {"HEALTHY", "AVAILABLE"}


def test_ffmpeg_status_matches_the_actual_binary(monkeypatch) -> None:
    """When FFmpeg is absent the system says UNAVAILABLE and refuses to render."""
    real = ffmpeg_info(refresh=True)

    monkeypatch.setattr(settings, "ffmpeg_binary", "definitely-not-ffmpeg-binary")
    missing = ffmpeg_info(refresh=True)
    assert missing.available is False
    assert "not found on PATH" in missing.detail

    try:
        require_ffmpeg()
    except FFmpegUnavailable as exc:
        assert "FFMPEG UNAVAILABLE" in exc.message
    else:  # pragma: no cover - guard against a silent regression
        raise AssertionError("require_ffmpeg must raise when the binary is missing")

    monkeypatch.undo()
    restored = ffmpeg_info(refresh=True)
    assert restored.available == real.available


def test_llm_availability_reports_missing_key_not_a_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    result = avail.llm_availability()
    assert result.status.value == "NOT CONFIGURED"
    assert result.missing_settings == ("ANTHROPIC_API_KEY",)
    assert result.usable is False


def test_voice_availability_uses_the_spec_wording(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_provider", "")
    result = avail.voice_availability()
    assert "VOICE PROVIDER NOT CONFIGURED" in result.detail


def test_unknown_provider_name_is_unavailable_not_silently_ignored(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "some-other-vendor")
    result = avail.llm_availability()
    assert result.status.value == "UNAVAILABLE"


def test_youtube_oauth_blocked_without_encryption_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_client_id", "client-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "client-secret")
    monkeypatch.setattr(settings, "encryption_key", "")
    result = avail.youtube_oauth_availability()
    assert result.status.value == "UNAVAILABLE"
    assert "ENCRYPTION_KEY" in result.detail
