"""Scanning, deduplication, persistence, rate limiting and source health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import Conflict, ValidationError
from nexora.db.models import AuditLog, Channel, TrendingTopic, TrendSource
from nexora.db.models.enums import TrendSourceKind
from nexora.services.trends import sources as source_service
from nexora.services.trends.scan import freshness_of, scan_channel
from tests.fixtures import feeds

FEED_A = "https://fixture-a.invalid/feed.xml"
FEED_B = "https://fixture-b.invalid/feed.xml"


def make_source(db: Session, channel: Channel, name: str, url: str, **kwargs) -> TrendSource:
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind=TrendSourceKind.RSS.value,
        name=name,
        config={"url": url, "category": "technology", "language": "en"},
        **kwargs,
    )
    db.commit()
    return source


# ------------------------------------------------------------------ source management
def test_create_source_validates_the_feed_url(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError):
        source_service.create_source(
            db, channel_id=channel.id, kind="rss", name="Bad", config={"url": "file:///etc/passwd"}
        )


def test_create_source_rejects_an_unknown_kind(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError):
        source_service.create_source(
            db, channel_id=channel.id, kind="telepathy", name="X", config={}
        )


def test_duplicate_source_name_conflicts(db: Session, channel: Channel) -> None:
    make_source(db, channel, "Feed", FEED_A)
    with pytest.raises(Conflict):
        source_service.create_source(
            db, channel_id=channel.id, kind="rss", name="Feed", config={"url": FEED_B}
        )


def test_minimum_interval_is_enforced(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError):
        source_service.create_source(
            db,
            channel_id=channel.id,
            kind="rss",
            name="Too eager",
            config={"url": FEED_A},
            min_interval_minutes=1,
        )


def test_seed_defaults_creates_rss_only_and_is_idempotent(db: Session, channel: Channel) -> None:
    created = source_service.seed_default_sources(db, channel.id)
    db.commit()
    assert len(created) == len(source_service.DEFAULT_RSS_SOURCES)
    assert {source.kind for source in created} == {"rss"}

    again = source_service.seed_default_sources(db, channel.id)
    db.commit()
    assert again == []


def test_source_health_reports_availability(db: Session, channel: Channel, monkeypatch) -> None:
    rss = make_source(db, channel, "RSS", FEED_A)
    assert source_service.source_availability(rss).status.value == "AVAILABLE"

    rss.enabled = False
    db.commit()
    assert source_service.source_availability(rss).status.value == "NOT CONNECTED"

    monkeypatch.setattr(settings, "youtube_api_key", "")
    youtube = source_service.create_source(
        db, channel_id=channel.id, kind="youtube_data_api", name="YT", config={"region_code": "US"}
    )
    db.commit()
    availability = source_service.source_availability(youtube)
    assert availability.status.value == "NOT CONFIGURED"
    assert availability.missing_settings == ("YOUTUBE_API_KEY",)


def test_source_config_redacts_credential_shaped_keys(db: Session, channel: Channel) -> None:
    source = make_source(db, channel, "RSS", FEED_A)
    source.config = {**source.config, "api_key": "super-secret-value"}
    db.commit()
    payload = source_service.source_to_dict(source)
    assert payload["config"]["api_key"] == "***REDACTED***"
    assert "super-secret-value" not in str(payload)


# ------------------------------------------------------------------------- scanning
@respx.mock
def test_scan_persists_normalized_items(db: Session, channel: Channel) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    source = make_source(db, channel, "Feed A", FEED_A)

    result = scan_channel(db, channel)
    db.commit()

    assert result.fetched == 2
    assert result.stored == 2
    rows = db.query(TrendingTopic).order_by(TrendingTopic.title).all()
    assert len(rows) == 2

    row = rows[0]
    assert row.source_id == source.id
    assert row.source_kind == "rss"
    assert row.discovered_at is not None
    assert row.content_hash
    assert row.duplicate_of_id is None
    assert row.corroboration_count == 1
    assert row.engagement == {}, "RSS reports no engagement; the map stays empty"
    assert row.opportunity_score is not None or row.score_breakdown["available"] is False


@respx.mock
def test_rescanning_the_same_feed_stores_nothing_new(db: Session, channel: Channel) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    make_source(db, channel, "Feed A", FEED_A)

    first = scan_channel(db, channel)
    db.commit()
    second = scan_channel(db, channel, force=True)
    db.commit()

    assert first.stored == 2
    assert second.stored == 0
    assert second.duplicates == 2
    assert db.query(TrendingTopic).count() == 2


@respx.mock
def test_the_same_story_from_two_sources_is_linked_not_duplicated(
    db: Session, channel: Channel
) -> None:
    """Cross-source dedup: a second telling becomes corroboration of the first."""
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    # Same headlines, different feed and different GUIDs.
    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, text=feeds.RSS_2_0.replace("fixture-item-", "other-item-"))
    )
    make_source(db, channel, "Feed A", FEED_A)
    make_source(db, channel, "Feed B", FEED_B)

    result = scan_channel(db, channel)
    db.commit()

    assert result.fetched == 4
    assert result.stored == 2, "only the first telling of each story is an original"
    assert result.duplicates == 2

    originals = db.query(TrendingTopic).filter(TrendingTopic.duplicate_of_id.is_(None)).all()
    duplicates = db.query(TrendingTopic).filter(TrendingTopic.duplicate_of_id.is_not(None)).all()
    assert len(originals) == 2
    assert len(duplicates) == 2
    # Every row is still stored — nothing is thrown away, it is linked.
    assert db.query(TrendingTopic).count() == 4
    assert all(original.corroboration_count == 2 for original in originals)


@respx.mock
def test_distinct_stories_are_not_merged(db: Session, channel: Channel) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    make_source(db, channel, "Feed A", FEED_A)
    scan_channel(db, channel)
    db.commit()

    hashes = {row.content_hash for row in db.query(TrendingTopic).all()}
    assert len(hashes) == 2, "two different headlines must not collide"


@respx.mock
def test_a_failing_source_records_the_failure_and_others_still_run(
    db: Session, channel: Channel
) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(500, text="boom"))
    respx.get(FEED_B).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    broken = make_source(db, channel, "Broken", FEED_A)
    working = make_source(db, channel, "Working", FEED_B)

    result = scan_channel(db, channel)
    db.commit()

    statuses = {item.source_name: item.status for item in result.sources}
    assert statuses == {"Broken": "FAILED", "Working": "SUCCESS"}
    assert result.stored == 2, "a broken source must not stop the others"

    db.refresh(broken)
    db.refresh(working)
    assert broken.last_status == "FAILED"
    assert broken.consecutive_failures == 1
    assert broken.next_allowed_at is not None
    assert broken.last_error
    assert working.consecutive_failures == 0


@respx.mock
def test_a_not_configured_source_is_reported_separately_from_a_failure(
    db: Session, channel: Channel, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "")
    source_service.create_source(
        db, channel_id=channel.id, kind="youtube_data_api", name="YT", config={"region_code": "US"}
    )
    db.commit()

    result = scan_channel(db, channel)
    db.commit()

    assert result.sources[0].status == "NOT_CONFIGURED"
    assert "YOUTUBE_API_KEY" in (result.sources[0].error or "")
    source = db.query(TrendSource).filter_by(name="YT").one()
    # A missing key is a configuration gap, not a failure to back off from.
    assert source.consecutive_failures == 0
    assert source.last_status == "NOT_CONFIGURED"


@respx.mock
def test_repeated_failures_extend_the_backoff(db: Session, channel: Channel) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(500, text="boom"))
    source = make_source(db, channel, "Broken", FEED_A)

    delays = []
    for _ in range(3):
        before = datetime.now(UTC)
        scan_channel(db, channel, force=True)
        db.commit()
        db.refresh(source)
        delays.append((source.next_allowed_at - before).total_seconds())

    assert source.consecutive_failures == 3
    assert delays == sorted(delays) and delays[0] < delays[-1]


@respx.mock
def test_bounded_caching_skips_a_source_that_is_not_due(db: Session, channel: Channel) -> None:
    route = respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    make_source(db, channel, "Feed A", FEED_A, min_interval_minutes=60)

    scan_channel(db, channel)
    db.commit()
    assert route.call_count == 1

    second = scan_channel(db, channel)
    db.commit()
    assert route.call_count == 1, "the upstream must not be hit again inside the interval"
    assert second.sources[0].status == "SKIPPED"
    assert "Not due yet" in (second.sources[0].error or "")

    third = scan_channel(db, channel, force=True)
    db.commit()
    assert route.call_count == 2
    assert third.sources[0].status == "SUCCESS"


def test_is_due_respects_the_configured_interval(db: Session, channel: Channel) -> None:
    source = make_source(db, channel, "Feed A", FEED_A, min_interval_minutes=30)
    now = datetime.now(UTC)
    assert source_service.is_due(source, now=now) is True

    source.last_run_at = now - timedelta(minutes=10)
    source.next_allowed_at = now + timedelta(minutes=20)
    assert source_service.is_due(source, now=now) is False
    assert source_service.is_due(source, now=now + timedelta(minutes=21)) is True


@respx.mock
def test_scan_is_audited(db: Session, channel: Channel) -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    make_source(db, channel, "Feed A", FEED_A)
    scan_channel(db, channel)
    db.commit()

    entry = db.query(AuditLog).filter(AuditLog.action == "trends.scan_completed").one()
    assert entry.channel_id == channel.id
    assert "stored 2" in (entry.summary or "")


# ------------------------------------------------------------------------ freshness
def test_freshness_uses_real_timestamps(db: Session, channel: Channel) -> None:
    now = datetime.now(UTC)
    row = TrendingTopic(
        source_id=make_source(db, channel, "Feed A", FEED_A).id,
        channel_id=channel.id,
        source_kind="rss",
        source_name="Feed A",
        external_id="x",
        dedupe_hash="d" * 64,
        content_hash="c" * 64,
        title="Fixture",
        discovered_at=now,
        published_at=now - timedelta(hours=2),
    )
    db.add(row)
    db.commit()

    fresh = freshness_of(row, now=now)
    assert fresh["state"] == "FRESH"
    assert fresh["basis"] == "published_at"
    assert 7100 <= fresh["age_seconds"] <= 7300

    row.published_at = now - timedelta(days=5)
    assert freshness_of(row, now=now)["state"] == "STALE"

    row.published_at = None
    unknown = freshness_of(row, now=now)
    assert unknown["basis"] == "discovered_at", "falls back to when we saw it, never guesses"
