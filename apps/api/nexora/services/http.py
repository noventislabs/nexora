"""Shared outbound HTTP client.

All third-party calls go through here so timeouts, redirect policy and the user agent
are consistent, and so failures are translated into typed application errors instead of
leaking transport exceptions into route handlers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable, RateLimited, UpstreamPermanentError

USER_AGENT = "nexora-autopilot/0.1 (+https://github.com/noventislabs/nexora)"

#: Upstream statuses that will never succeed on retry.
PERMANENT_STATUSES = {400, 401, 403, 404, 405, 409, 410, 422}


@contextmanager
def http_client(
    *, timeout: float | None = None, headers: dict[str, str] | None = None
) -> Iterator[httpx.Client]:
    merged = {"user-agent": USER_AGENT, **(headers or {})}
    with httpx.Client(
        timeout=timeout or settings.http_timeout_seconds,
        follow_redirects=True,
        headers=merged,
    ) as client:
        yield client


def raise_for_upstream(response: httpx.Response, *, provider: str) -> None:
    """Translate an HTTP error into the right kind of application error.

    The distinction matters to the queue: a permanent error must not consume retries.
    """
    if response.is_success:
        return

    detail = _error_detail(response)
    if response.status_code == 429:
        raise RateLimited(
            f"{provider} rate limit reached. {detail}".strip(),
            details={"provider": provider, "retry_after": response.headers.get("retry-after")},
        )
    if response.status_code in PERMANENT_STATUSES:
        raise UpstreamPermanentError(
            f"{provider} rejected the request with HTTP {response.status_code}. {detail}".strip(),
            details={"provider": provider, "status": response.status_code},
        )
    raise ProviderUnavailable(
        f"{provider} returned HTTP {response.status_code}. {detail}".strip(),
        details={"provider": provider, "status": response.status_code},
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or "")[:300]
        if isinstance(error, str):
            description = payload.get("error_description")
            return f"{error}: {description}"[:300] if description else error[:300]
        message = payload.get("message") or payload.get("detail")
        if message:
            return str(message)[:300]
    return str(payload)[:300]


def request_json(
    client: httpx.Client, method: str, url: str, *, provider: str, **kwargs: Any
) -> Any:
    """Perform a request and return parsed JSON, or raise a typed error."""
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailable(f"{provider} timed out.", details={"provider": provider}) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(
            f"{provider} is unreachable: {exc}", details={"provider": provider}
        ) from exc
    raise_for_upstream(response, provider=provider)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderUnavailable(
            f"{provider} returned a response that is not JSON.", details={"provider": provider}
        ) from exc
