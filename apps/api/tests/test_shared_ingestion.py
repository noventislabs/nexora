"""Shared trend ingestion: one crawl, many channels, separate rankings.

The architecture the product requires is global ingestion into a normalized database,
then per-channel ranking — not one isolated crawler per channel. These tests hold that
shape, and hold the boundary that stops one channel's data reaching another's.
"""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.db.models import ChannelTopicRelevance, TrendingTopic, User
from nexora.db.models.enums import RelevanceStatus, SourceScope, TrendSourceKind
from nexora.services import channels as channel_service
from nexora.services import profiles as profile_service
from nexora.services.trends import sources as source_service
from nexora.services.trends.scan import scan_channel
from tests.fixtures import feeds

FEED = "https://fixture-shared.invalid/feed.xml"


def shared_source(db: Session, channel, user: User, name: str = "Shared fixture feed"):
    return source_service.create_source(
        db,
        channel_id=channel.id,
        kind=TrendSourceKind.RSS.value,
        name=name,
        config={"url": FEED, "category": "technology", "language": "en"},
        scope=SourceScope.SHARED.value,
        user_id=user.id,
    )


@respx.mock
def test_a_shared_scan_ingests_once_and_ranks_for_every_channel(
    db: Session, user: User
) -> None:
    tech = channel_service.create_channel(
        db, user, name="Tech Desk", categories=["technology", "ai"]
    )
    kids = channel_service.create_channel(db, user, name="Kids Tales", categories=["kids"])
    db.flush()

    shared_source(db, tech, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))

    result = scan_channel(db, tech, user_id=user.id)
    db.commit()

    assert result.stored > 0
    rows = db.query(TrendingTopic).all()
    # Stored once, owned by the user rather than by whichever channel scanned.
    assert len(rows) == result.stored
    assert all(row.channel_id is None for row in rows)
    assert all(row.user_id == user.id for row in rows)

    # …and ranked separately for both channels.
    for channel in (tech, kids):
        records = db.query(ChannelTopicRelevance).filter_by(channel_id=channel.id).all()
        assert len(records) == len(rows), channel.name


@respx.mock
def test_a_second_channel_scanning_the_same_shared_feed_stores_nothing_new(
    db: Session, user: User
) -> None:
    """Deduplication spans the account, not just one channel."""
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()

    source = shared_source(db, first, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))

    first_run = scan_channel(db, first, user_id=user.id)
    db.commit()
    assert first_run.stored > 0

    # Make the source due again so the second channel actually fetches it.
    source.next_allowed_at = None
    source.last_run_at = None
    db.commit()

    second_run = scan_channel(db, second, user_id=user.id)
    db.commit()

    assert second_run.stored == 0
    assert second_run.duplicates > 0
    assert db.query(TrendingTopic).count() == first_run.stored


@respx.mock
def test_a_channel_scoped_source_stays_with_its_channel(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()

    source_service.create_source(
        db,
        channel_id=first.id,
        kind=TrendSourceKind.RSS.value,
        name="Private fixture feed",
        config={"url": FEED, "category": "technology", "language": "en"},
    )
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))

    scan_channel(db, first, user_id=user.id)
    db.commit()

    rows = db.query(TrendingTopic).all()
    assert rows and all(row.channel_id == first.id for row in rows)
    # The second channel ranked none of them, because it can see none of them.
    assert db.query(ChannelTopicRelevance).filter_by(channel_id=second.id).count() == 0


@respx.mock
def test_each_channels_blocked_topics_apply_only_to_itself(db: Session, user: User) -> None:
    permissive = channel_service.create_channel(
        db, user, name="Permissive", categories=["technology", "ai"]
    )
    restrictive = channel_service.create_channel(
        db, user, name="Restrictive", categories=["technology", "ai"]
    )
    db.flush()
    profile_service.update_profile(db, restrictive, {"blocked_topics": ["ai"]})

    shared_source(db, permissive, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))

    scan_channel(db, permissive, user_id=user.id)
    db.commit()

    restricted = db.query(ChannelTopicRelevance).filter_by(channel_id=restrictive.id).all()
    permitted = db.query(ChannelTopicRelevance).filter_by(channel_id=permissive.id).all()

    excluded = [r for r in restricted if r.status == RelevanceStatus.EXCLUDED.value]
    assert excluded, "the restrictive channel should have excluded the AI items"
    # The same items are not excluded for the channel that did not block them.
    excluded_topics = {r.trending_topic_id for r in excluded}
    for record in permitted:
        if record.trending_topic_id in excluded_topics:
            assert record.status != RelevanceStatus.EXCLUDED.value


def test_a_shared_source_must_name_its_owner(db: Session, channel) -> None:
    import pytest

    from nexora.core.errors import ValidationError

    with pytest.raises(ValidationError, match="must name the user"):
        source_service.create_source(
            db,
            channel_id=channel.id,
            kind=TrendSourceKind.RSS.value,
            name="Ownerless",
            config={"url": FEED},
            scope=SourceScope.SHARED.value,
        )


def test_list_sources_includes_shared_ones_for_the_owner(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()
    shared = shared_source(db, first, user)
    db.commit()

    for channel in (first, second):
        names = {row.id for row in source_service.list_sources(db, channel.id, user_id=user.id)}
        assert shared.id in names

    # Without the owner, only the channel's own sources are returned.
    assert shared.id not in {row.id for row in source_service.list_sources(db, second.id)}


# ------------------------------------------------------------------------- API
@respx.mock
def test_the_trends_api_ranks_for_the_requesting_channel(
    auth_client: TestClient, db: Session, user: User
) -> None:
    tech = channel_service.create_channel(db, user, name="Tech Desk", categories=["ai"])
    kids = channel_service.create_channel(db, user, name="Kids Tales", categories=["kids"])
    db.flush()
    shared_source(db, tech, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))
    scan_channel(db, tech, user_id=user.id)
    db.commit()

    tech_body = auth_client.get(f"/api/trends?channel_id={tech.id}&limit=50").json()
    kids_body = auth_client.get(f"/api/trends?channel_id={kids.id}&limit=50").json()

    assert tech_body["total"] > 0
    assert "rank differently for another" in tech_body["ranking_note"]

    tech_scores = {
        item["id"]: item["opportunity_score"]
        for item in tech_body["items"]
        if item["opportunity_score"] is not None
    }
    assert tech_scores, "the technology channel should have scored items"

    # The same items are visible to the kids channel and score lower or not at all.
    for item in kids_body["items"]:
        if item["id"] in tech_scores and item["opportunity_score"] is not None:
            assert item["opportunity_score"] <= tech_scores[item["id"]]


@respx.mock
def test_the_relevance_endpoint_explains_the_verdict(
    auth_client: TestClient, db: Session, user: User
) -> None:
    channel = channel_service.create_channel(db, user, name="Kids", categories=["kids"])
    db.flush()
    shared_source(db, channel, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))
    scan_channel(db, channel, user_id=user.id)
    db.commit()

    row = db.query(TrendingTopic).first()
    body = auth_client.get(f"/api/channels/{channel.id}/relevance/{row.id}").json()

    assert body["trending_topic_id"] == str(row.id)
    assert body["relevance"]["status"] in {
        RelevanceStatus.RELEVANT.value,
        RelevanceStatus.LOW_RELEVANCE.value,
        RelevanceStatus.INSUFFICIENT_DATA.value,
    }
    assert body["relevance"]["relevance_reasons"]
    # The inputs the verdict was reached from, so a person can fix them.
    assert body["matching_inputs"]["primary_categories"] == ["kids"]


@respx.mock
def test_recomputing_relevance_re_ranks_after_a_profile_change(
    auth_client: TestClient, db: Session, user: User
) -> None:
    channel = channel_service.create_channel(db, user, name="Tech", categories=["technology", "ai"])
    db.flush()
    shared_source(db, channel, user)
    db.commit()
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_TECH))
    scan_channel(db, channel, user_id=user.id)
    db.commit()

    auth_client.patch(
        f"/api/channels/{channel.id}/profile", json={"blocked_topics": ["ai", "chip"]}
    )
    body = auth_client.post(f"/api/channels/{channel.id}/relevance/recompute").json()

    assert body["evaluated"] > 0
    assert body["by_status"].get(RelevanceStatus.EXCLUDED.value, 0) > 0

    # Excluded items disappear from the ranked list by default.
    listed = auth_client.get(f"/api/trends?channel_id={channel.id}&limit=50").json()
    assert listed["total"] < body["evaluated"]
