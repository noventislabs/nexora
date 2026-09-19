"""Typed application errors mapped to HTTP responses by the API layer."""

from __future__ import annotations

from typing import Any


class NexoraError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(NexoraError):
    status_code = 422
    code = "validation_error"


class AuthenticationRequired(NexoraError):
    status_code = 401
    code = "authentication_required"


class PermissionDenied(NexoraError):
    status_code = 403
    code = "permission_denied"


class NotFound(NexoraError):
    status_code = 404
    code = "not_found"


class Conflict(NexoraError):
    status_code = 409
    code = "conflict"


class RateLimited(NexoraError):
    status_code = 429
    code = "rate_limited"


class ProviderNotConfigured(NexoraError):
    """A required external integration has no credentials configured.

    The API surfaces this as ``NOT_CONFIGURED`` so the UI can render an explicit
    unavailable state rather than inventing a result.
    """

    status_code = 503
    code = "provider_not_configured"


class ProviderUnavailable(NexoraError):
    """A configured external integration failed or is unreachable."""

    status_code = 502
    code = "provider_unavailable"


class UpstreamPermanentError(ProviderUnavailable):
    """An upstream rejected the request in a way that will not succeed on retry."""

    code = "upstream_permanent_error"


class SafetyBlocked(NexoraError):
    """An automation guardrail (kill switch, limit, quality gate) blocked the action."""

    status_code = 409
    code = "safety_blocked"
