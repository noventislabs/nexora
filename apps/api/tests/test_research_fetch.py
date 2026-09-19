"""Source fetching: robots.txt, SSRF guards, extraction and failure handling."""

from __future__ import annotations

import httpx
import pytest
import respx

from nexora.core.errors import ValidationError
from nexora.services.research import fetch as fetch_module
from tests.fixtures import feeds

PAGE_URL = "https://fixture-news.invalid/articles/1"
ROBOTS_URL = "https://fixture-news.invalid/robots.txt"


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    fetch_module.reset_robots_cache()
    yield
    fetch_module.reset_robots_cache()


def test_url_validation_blocks_non_http_schemes() -> None:
    for bad in ("file:///etc/passwd", "ftp://host/x", "javascript:alert(1)", ""):
        with pytest.raises(ValidationError):
            fetch_module.validate_source_url(bad)


def test_url_validation_blocks_private_and_metadata_addresses() -> None:
    """Operator-supplied URLs are fetched server-side, so this is an SSRF boundary."""
    for bad in (
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
    ):
        with pytest.raises(ValidationError):
            fetch_module.validate_source_url(bad)


def test_fetching_is_off_unless_the_channel_enables_it() -> None:
    result = fetch_module.fetch_document(PAGE_URL, enabled=False)
    assert result.decision == fetch_module.DISABLED
    assert result.text is None
    assert "off for this channel" in result.note


@respx.mock
def test_robots_disallow_blocks_the_fetch() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_DISALLOW_ALL))
    page = respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=feeds.SOURCE_PAGE_HTML))

    result = fetch_module.fetch_document(PAGE_URL, enabled=True)

    assert result.decision == fetch_module.BLOCKED_BY_ROBOTS
    assert result.text is None
    assert page.call_count == 0, "the page must not be requested at all"
    assert "disallows" in result.note


@respx.mock
def test_robots_allow_permits_the_fetch_and_extracts_text() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(
            200, text=feeds.SOURCE_PAGE_HTML, headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)

    assert result.decision == fetch_module.ALLOWED
    assert result.succeeded
    assert result.title == "TEST FIXTURE: fabrication capacity report"
    assert "40,000 wafer starts" in (result.text or "")
    assert "var tracker" not in (result.text or ""), "scripts must be stripped"
    assert "<p>" not in (result.text or "")
    assert result.checksum and result.fetched_at


@respx.mock
def test_absent_robots_is_treated_as_permitted_and_says_so() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(200, text=feeds.SOURCE_PAGE_HTML,
                                    headers={"content-type": "text/html"})
    )
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)
    assert result.decision == fetch_module.ALLOWED
    assert "No robots.txt" in result.note


@respx.mock
def test_robots_is_fetched_once_per_origin() -> None:
    robots = respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL)
    )
    respx.get(url__startswith="https://fixture-news.invalid/articles/").mock(
        return_value=httpx.Response(200, text=feeds.SOURCE_PAGE_HTML,
                                    headers={"content-type": "text/html"})
    )
    fetch_module.fetch_document(PAGE_URL, enabled=True)
    fetch_module.fetch_document("https://fixture-news.invalid/articles/2", enabled=True)
    assert robots.call_count == 1


@respx.mock
def test_http_error_is_recorded_not_swallowed() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL))
    respx.get(PAGE_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)
    assert result.decision == fetch_module.FAILED
    assert result.http_status == 503
    assert result.text is None


@respx.mock
def test_non_html_content_is_refused() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)
    assert result.decision == fetch_module.FAILED
    assert "Unsupported content type" in result.note


@respx.mock
def test_timeout_is_recorded() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL))
    respx.get(PAGE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)
    assert result.decision == fetch_module.FAILED
    assert result.error


@respx.mock
def test_long_pages_are_truncated_and_marked() -> None:
    long_html = "<html><body>" + ("<p>word word word</p>" * 20000) + "</body></html>"
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(200, text=long_html, headers={"content-type": "text/html"})
    )
    result = fetch_module.fetch_document(PAGE_URL, enabled=True)
    assert result.truncated is True
    assert len(result.text or "") <= fetch_module.MAX_TEXT_CHARS


def test_html_to_text_unescapes_and_collapses() -> None:
    text = fetch_module.html_to_text("<p>A &amp; B</p><p>C&nbsp;D</p>")
    assert "&amp;" not in text
    assert "A & B" in text
