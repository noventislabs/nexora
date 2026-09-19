"""Security properties: encryption, secret redaction, headers and input validation."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from nexora.config import settings
from nexora.core.crypto import (
    DecryptionError,
    EncryptionNotConfigured,
    decrypt_str,
    encrypt_str,
    encryption_available,
    token_fingerprint,
)
from nexora.core.errors import ValidationError
from nexora.core.logging import JsonFormatter, SecretRedactingFilter
from nexora.services.providers.storage.local import LocalStorageProvider


def test_encryption_requires_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "")
    assert encryption_available() is False
    with pytest.raises(EncryptionNotConfigured):
        encrypt_str("oauth-refresh-token")


def test_round_trip_encryption(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    secret = "1//0abcdefgh-refresh-token"
    sealed = encrypt_str(secret)
    assert secret not in sealed
    assert decrypt_str(sealed) == secret


def test_ciphertext_from_another_key_cannot_be_read(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    sealed = encrypt_str("token")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        decrypt_str(sealed)


def test_token_fingerprint_is_deterministic_and_not_reversible() -> None:
    token = "a-session-token"
    digest = token_fingerprint(token)
    assert digest == token_fingerprint(token)
    assert token not in digest
    assert len(digest) == 64


def test_log_filter_redacts_configured_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-supersecretvalue12345")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="calling provider with key sk-supersecretvalue12345", args=(), exc_info=None,
    )
    assert SecretRedactingFilter().filter(record) is True
    rendered = JsonFormatter().format(record)
    assert "sk-supersecretvalue12345" not in rendered
    assert "***REDACTED***" in rendered


def test_log_filter_redacts_secrets_in_extra_fields(monkeypatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-abcdefghijklmnop")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="provider call", args=(), exc_info=None,
    )
    record.api_key = "el-abcdefghijklmnop"
    SecretRedactingFilter().filter(record)
    assert "el-abcdefghijklmnop" not in JsonFormatter().format(record)


def test_storage_key_traversal_is_rejected(tmp_path) -> None:
    provider = LocalStorageProvider(root=str(tmp_path))
    for bad_key in ("../escape.txt", "a/../../escape.txt", "/etc/passwd", ""):
        with pytest.raises(ValidationError):
            provider.put(bad_key, b"data")


def test_storage_round_trip_and_checksum(tmp_path) -> None:
    import hashlib

    provider = LocalStorageProvider(root=str(tmp_path))
    payload = b"nexora render bytes"
    stored = provider.put("renders/project/final.mp4", payload, content_type="video/mp4")
    assert stored.size_bytes == len(payload)
    assert stored.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert provider.get("renders/project/final.mp4") == payload
    assert b"".join(provider.stream("renders/project/final.mp4")) == payload
    provider.delete("renders/project/final.mp4")
    assert provider.exists("renders/project/final.mp4") is False


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/api/system/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


def test_validation_errors_do_not_echo_submitted_values(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "x@y.test", "password": "tiny", "display_name": "X"},
    )
    assert response.status_code == 422
    assert "tiny" not in response.text, "submitted credentials must never be echoed back"


def test_unknown_body_fields_are_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/api/channels", json={"name": "X", "is_admin": True})
    assert response.status_code == 422


def test_ffmpeg_arguments_reject_shell_metacharacters() -> None:
    from nexora.services.ffmpeg_runtime import validate_args

    validate_args(["-i", "input.mp4", "-c:v", "libx264"])
    for bad in ("a; rm -rf /", "a | cat", "$(whoami)", "a && b", "a`b`"):
        with pytest.raises(ValidationError):
            validate_args(["-i", bad])
