"""Fetching source material for research.

Policy, enforced in code rather than promised in docs:

* A URL is retrieved only when the operator has enabled full-text fetching for that
  channel **and** the host's ``robots.txt`` permits it for our user agent.
* Every outcome is recorded — including refusals — so a research run can always show
  what it was and was not allowed to read.
* Only a bounded excerpt is stored. NEXORA keeps source text to ground and verify
  claims, not to republish it; scripts are written in original wording and the
  originality check (Phase 4) compares against exactly these excerpts.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from nexora.core.errors import ValidationError
from nexora.core.logging import get_logger
from nexora.services.http import USER_AGENT, http_client

logger = get_logger(__name__)

#: Upper bound on stored text per document. Enough to verify claims, far short of a copy.
MAX_TEXT_CHARS = 20_000
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 20.0
ROBOTS_CACHE_TTL_SECONDS = 3600

ALLOWED = "ALLOWED"
BLOCKED_BY_ROBOTS = "BLOCKED_BY_ROBOTS"
NOT_ATTEMPTED = "NOT_ATTEMPTED"
DISABLED = "DISABLED"
FAILED = "FAILED"

_robots_lock = threading.Lock()
_robots_cache: dict[str, tuple[robotparser.RobotFileParser | None, float]] = {}

_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


@dataclass
class FetchResult:
    decision: str
    url: str
    note: str
    text: str | None = None
    title: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    fetched_at: datetime | None = None
    truncated: bool = False
    checksum: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.decision == ALLOWED and bool(self.text)


def validate_source_url(url: str) -> str:
    """Only http(s) with a host, and never a private or loopback address.

    Blocking private ranges matters because a research source URL is operator-supplied
    input that the server then fetches: without this, it is an SSRF primitive.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Source URL must use http or https.")
    if not parsed.hostname:
        raise ValidationError("Source URL must include a host.")
    if _is_private_host(parsed.hostname):
        raise ValidationError("Source URL must not point at a private or loopback address.")
    return parsed.geturl()


def _is_private_host(hostname: str) -> bool:
    import ipaddress
    import socket

    lowered = hostname.lower()
    if lowered in ("localhost", "localhost.localdomain") or lowered.endswith(".localhost"):
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        # Unresolvable: let the fetch fail normally rather than guess.
        return False
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or parsed.is_unspecified
        ):
            return True
    return False


def robots_allows(url: str, *, user_agent: str = USER_AGENT) -> tuple[bool, str]:
    """Consult the host's robots.txt. Returns (allowed, explanation).

    A robots.txt that cannot be retrieved is treated as *permitting* the fetch, which
    is the behaviour the standard describes; the explanation records that it was absent
    so the decision is never silently assumed.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(origin, "/robots.txt")

    with _robots_lock:
        cached = _robots_cache.get(origin)
        if cached and cached[1] > time.monotonic():
            parser = cached[0]
            if parser is None:
                return True, "No robots.txt was served by this host."
            allowed = parser.can_fetch(user_agent, url)
            return allowed, _robots_note(allowed, robots_url)

    parser: robotparser.RobotFileParser | None = None
    try:
        with http_client(timeout=10.0) as client:
            response = client.get(robots_url)
        if response.status_code == 200:
            parser = robotparser.RobotFileParser()
            parser.parse(response.text.splitlines())
    except httpx.HTTPError as exc:
        logger.info("research.robots_unreachable", extra={"origin": origin, "error": str(exc)})

    with _robots_lock:
        _robots_cache[origin] = (parser, time.monotonic() + ROBOTS_CACHE_TTL_SECONDS)

    if parser is None:
        return True, "No robots.txt was served by this host."
    allowed = parser.can_fetch(user_agent, url)
    return allowed, _robots_note(allowed, robots_url)


def _robots_note(allowed: bool, robots_url: str) -> str:
    return (
        f"{robots_url} permits {USER_AGENT} to fetch this path."
        if allowed
        else f"{robots_url} disallows {USER_AGENT} for this path; the page was not fetched."
    )


def reset_robots_cache() -> None:
    with _robots_lock:
        _robots_cache.clear()


def fetch_document(url: str, *, enabled: bool) -> FetchResult:
    """Retrieve a source page, if and only if that is permitted."""
    if not enabled:
        return FetchResult(
            decision=DISABLED,
            url=url,
            note=(
                "Full-text fetching is off for this channel, so only the title, summary "
                "and metadata the feed already provided were used."
            ),
        )

    try:
        safe_url = validate_source_url(url)
    except ValidationError as exc:
        return FetchResult(decision=FAILED, url=url, note=str(exc), error=str(exc))

    allowed, note = robots_allows(safe_url)
    if not allowed:
        logger.info("research.robots_blocked", extra={"url": safe_url})
        return FetchResult(decision=BLOCKED_BY_ROBOTS, url=safe_url, note=note)

    try:
        with http_client(
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
        ) as client:
            response = client.get(safe_url)
    except httpx.HTTPError as exc:
        return FetchResult(
            decision=FAILED, url=safe_url, note=f"Fetch failed: {exc}", error=str(exc)
        )

    content_type = response.headers.get("content-type", "")
    if response.status_code != 200:
        return FetchResult(
            decision=FAILED,
            url=safe_url,
            note=f"Host returned HTTP {response.status_code}.",
            http_status=response.status_code,
            content_type=content_type,
            error=f"HTTP {response.status_code}",
        )
    if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml")):
        return FetchResult(
            decision=FAILED,
            url=safe_url,
            note=f"Unsupported content type '{content_type or 'unknown'}'.",
            http_status=response.status_code,
            content_type=content_type,
            error="unsupported content type",
        )

    raw = response.content[:MAX_RESPONSE_BYTES]
    html = raw.decode(response.encoding or "utf-8", errors="replace")
    title = extract_title(html)
    text = html_to_text(html)
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]

    return FetchResult(
        decision=ALLOWED,
        url=safe_url,
        note=note,
        text=text or None,
        title=title,
        http_status=response.status_code,
        content_type=content_type,
        fetched_at=datetime.now(UTC),
        truncated=truncated,
        checksum=hashlib.sha256(raw).hexdigest(),
    )


def extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return _collapse(_TAG_RE.sub(" ", match.group(1)))[:500] or None


def html_to_text(html: str) -> str:
    """A deliberately plain HTML-to-text pass.

    No readability heuristics: guessing which block is "the article" would silently
    drop context the fact checker may need, and a slightly noisier excerpt is the safer
    failure.
    """
    body = _SCRIPT_RE.sub(" ", html)
    body = re.sub(r"<(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", body, flags=re.IGNORECASE)
    body = _TAG_RE.sub(" ", body)
    body = _unescape(body)
    body = _WS_RE.sub(" ", body)
    body = "\n".join(line.strip() for line in body.splitlines())
    return _BLANKS_RE.sub("\n\n", body).strip()


def _unescape(text: str) -> str:
    import html as html_module

    return html_module.unescape(text)


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", _unescape(text)).strip()


def word_count(text: str | None) -> int | None:
    if not text:
        return None
    return len(text.split())
