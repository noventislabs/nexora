"""End-to-end trend scan over a real HTTP socket.

Unlike ``test_trend_scan.py``, nothing here is intercepted: a real HTTP server serves a
fixture feed on localhost and the provider fetches it over a real TCP connection. This
exercises the whole path — socket, HTTP client, feed parse, normalization,
deduplication, scoring and persistence — with no mocking layer at all.

The feed *content* is a clearly-labelled fixture; the transport is genuine.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy.orm import Session

from nexora.db.models import Channel, TrendingTopic
from nexora.services.trends import sources as source_service
from nexora.services.trends.scan import scan_channel
from tests.fixtures import feeds


class _FeedHandler(BaseHTTPRequestHandler):
    """Serves the fixture feeds, plus routes that exercise real failure modes."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/rss.xml":
            body = feeds.RSS_2_0.encode()
            content_type = "application/rss+xml; charset=utf-8"
        elif self.path == "/atom.xml":
            body = feeds.ATOM_1_0.encode()
            content_type = "application/atom+xml; charset=utf-8"
        elif self.path == "/boom":
            self.send_error(500, "fixture server error")
            return
        else:
            self.send_error(404, "not found")
            return

        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


@pytest.fixture(scope="module")
def feed_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_scan_over_a_real_socket_persists_and_scores(
    db: Session, channel: Channel, feed_server: str
) -> None:
    source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Local RSS",
        config={"url": f"{feed_server}/rss.xml", "category": "technology", "language": "en"},
    )
    source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Local Atom",
        config={"url": f"{feed_server}/atom.xml", "category": "science", "language": "en"},
    )
    db.commit()

    result = scan_channel(db, channel)
    db.commit()

    assert {item.status for item in result.sources} == {"SUCCESS"}
    assert result.fetched == 4
    assert result.stored == 4

    rows = db.query(TrendingTopic).all()
    assert len(rows) == 4
    assert all(row.discovered_at is not None for row in rows)
    assert all(row.content_hash for row in rows)
    # Two distinct feeds, four distinct stories: nothing should be merged.
    assert len({row.content_hash for row in rows}) == 4
    assert {row.source_name for row in rows} == {"Local RSS", "Local Atom"}

    # Scoring ran for real: every row carries a breakdown that explains itself.
    for row in rows:
        assert row.scored_at is not None
        assert row.signal_breakdown is not None
        assert row.signal_breakdown["method"]
        components = {c["key"]: c for c in row.signal_breakdown["components"]}
        # RSS publishes no engagement, so velocity must be unavailable, not zero.
        assert components["trend_velocity"]["available"] is False
        assert components["trend_velocity"]["value"] is None
        # Recency is computable because these feeds carry publication dates.
        assert components["recency"]["available"] is True


def test_a_real_http_500_is_recorded_as_a_source_failure(
    db: Session, channel: Channel, feed_server: str
) -> None:
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Broken Local",
        config={"url": f"{feed_server}/boom"},
    )
    db.commit()

    result = scan_channel(db, channel)
    db.commit()

    assert result.sources[0].status == "FAILED"
    assert result.stored == 0
    db.refresh(source)
    assert source.consecutive_failures == 1
    assert "500" in (source.last_error or "")
    # A failed fetch stores nothing — there is no substitute content.
    assert db.query(TrendingTopic).count() == 0


def test_a_real_connection_refusal_is_reported_not_swallowed(
    db: Session, channel: Channel
) -> None:
    # Port 1 on localhost has nothing listening: a genuine connection refusal.
    source_service.create_source(
        db, channel_id=channel.id, kind="rss", name="Dead Port", config={"url": "http://127.0.0.1:1/feed"}
    )
    db.commit()

    result = scan_channel(db, channel)
    db.commit()

    assert result.sources[0].status == "FAILED"
    assert result.sources[0].error
    assert db.query(TrendingTopic).count() == 0
