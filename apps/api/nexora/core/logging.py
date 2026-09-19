"""Structured JSON logging.

Secrets are never logged: :class:`SecretRedactingFilter` scrubs any value that
matches a configured credential before the record reaches a handler.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from nexora.config import settings

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}

SENSITIVE_SETTING_FIELDS = (
    "app_secret", "encryption_key", "storage_secret_key", "storage_access_key",
    "anthropic_api_key", "openai_api_key", "elevenlabs_api_key",
    "youtube_client_secret", "youtube_api_key", "reddit_client_secret",
)


def _secret_values() -> list[str]:
    values = []
    for field in SENSITIVE_SETTING_FIELDS:
        value = getattr(settings, field, "")
        if isinstance(value, str) and len(value) >= 8:
            values.append(value)
    return values


class SecretRedactingFilter(logging.Filter):
    """Replaces any known credential substring with ``***REDACTED***``."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = _secret_values()
        if not secrets:
            return True
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        for secret in secrets:
            if secret in rendered:
                rendered = rendered.replace(secret, "***REDACTED***")
                record.msg = rendered
                record.args = ()
        for key, value in list(record.__dict__.items()):
            if isinstance(value, str):
                for secret in secrets:
                    if secret in value:
                        record.__dict__[key] = value.replace(secret, "***REDACTED***")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    for noisy in ("uvicorn.access", "httpx", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
