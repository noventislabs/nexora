"""Encryption helpers for credentials held at rest.

OAuth refresh/access tokens are never persisted in plaintext. They are sealed with
Fernet (AES-128-CBC + HMAC-SHA256) using ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from nexora.config import settings


class EncryptionNotConfigured(RuntimeError):
    """Raised when an encryption operation is attempted without ENCRYPTION_KEY."""


class DecryptionError(RuntimeError):
    """Raised when stored ciphertext cannot be decrypted with the current key."""


def encryption_available() -> bool:
    try:
        _fernet()
    except EncryptionNotConfigured:
        return False
    return True


def _fernet() -> Fernet:
    key = settings.encryption_key.strip()
    if not key:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet;"
            "print(Fernet.generate_key().decode())\"`."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY is not a valid 32-byte urlsafe-base64 Fernet key."
        ) from exc


def encrypt_str(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_str(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Stored credential could not be decrypted with the current ENCRYPTION_KEY."
        ) from exc


def new_token(nbytes: int = 32) -> str:
    """A cryptographically random, URL-safe token."""
    return secrets.token_urlsafe(nbytes)


def token_fingerprint(token: str) -> str:
    """Deterministic lookup hash for opaque tokens stored in the database.

    Session tokens are stored only as a keyed digest; a database leak therefore does
    not yield usable session credentials.
    """
    key = (settings.app_secret or settings.encryption_key or "nexora-dev").encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")
