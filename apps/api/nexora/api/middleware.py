"""Request-scoped middleware: request IDs, access logs and a simple rate limiter."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from nexora.config import settings
from nexora.core.logging import get_logger

logger = get_logger("nexora.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request.error",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        duration = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        logger.info(
            "request.completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration, 2),
                "user_id": getattr(request.state, "user_id", None),
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("referrer-policy", "no-referrer")
        response.headers.setdefault("cross-origin-opener-policy", "same-origin")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter, strictest on authentication.

    In-process by design: it protects a single-host deployment without adding a
    dependency. A multi-instance deployment should front this with a shared limiter.
    """

    #: Paths where credential-guessing is the risk, limited far more tightly.
    AUTH_PATHS = ("/api/auth/login", "/api/auth/register")

    def __init__(self, app, *, default_limit: int | None = None, auth_limit: int | None = None,
                 window: int = 60):
        super().__init__(app)
        self.default_limit = default_limit or settings.rate_limit_per_minute
        self.auth_limit = auth_limit or settings.auth_rate_limit_per_minute
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _bucket(self, request: Request) -> tuple[str, int]:
        client = request.client.host if request.client else "unknown"
        path = request.url.path
        if path.startswith(self.AUTH_PATHS):
            return f"auth:{client}:{path}", self.auth_limit
        return f"api:{client}", self.default_limit

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            not settings.rate_limit_enabled
            or request.method == "OPTIONS"
            or not request.url.path.startswith("/api/")
        ):
            return await call_next(request)

        key, limit = self._bucket(request)
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = int(self.window - (now - bucket[0])) + 1
            logger.warning(
                "request.rate_limited",
                extra={"path": request.url.path, "limit": limit, "window_seconds": self.window},
            )
            return JSONResponse(
                status_code=429,
                content={
                    "code": "rate_limited",
                    "message": f"Too many requests. Try again in {retry_after}s.",
                },
                headers={"retry-after": str(retry_after)},
            )
        bucket.append(now)
        return await call_next(request)
