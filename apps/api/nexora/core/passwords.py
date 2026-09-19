"""Password hashing (Argon2id)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Tuned to stay comfortably within an 8 GB / i3 development machine while remaining
# materially more expensive than a plain KDF for an attacker.
_hasher = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

MIN_PASSWORD_LENGTH = 12


class WeakPassword(ValueError):
    pass


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password.strip() == "":
        raise WeakPassword("Password must not be blank.")


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
