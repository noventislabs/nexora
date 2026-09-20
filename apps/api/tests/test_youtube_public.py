"""Public channel reads via the Data API key, and their strict separation from OAuth."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import NotFound, ProviderNotConfigured, ValidationError
from nexora.db.models import AnalyticsSnapshot, AuditLog, Channel
from nexora.services.youtube import public as public_service
from tests.fixtures import feeds

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-data-api-key")


def test_without_an_api_key_public_reads_are_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "")
    with pytest.raises(ProviderNotConfigured) as exc:
        public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00")
    assert "YOUTUBE_API_KEY" in str(exc.value.details)


def test_channel_id_normalization() -> None:
    expected = "UCFIXTUREPUBLICCHANNEL00"
    assert public_service.normalize_channel_id(expected) == expected
    assert public_service.normalize_channel_id("FIXTUREPUBLICCHANNEL00") == expected
    assert (
        public_service.normalize_channel_id(
            f"https://www.youtube.com/channel/{expected}?view=0"
        )
        == expected
    )


def test_a_handle_is_not_a_channel_id() -> None:
    with pytest.raises(ValidationError) as exc:
        public_service.normalize_channel_id("@somechannel")
    assert "handle" in exc.value.message


def test_nonsense_identifiers_are_rejected() -> None:
    for bad in ("", "nonsense", "UC-too-short", "x" * 40):
        with pytest.raises(ValidationError):
            public_service.normalize_channel_id(bad)


@respx.mock
def test_a_public_read_returns_only_what_the_api_gave(api_key) -> None:
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_PUBLIC_CHANNEL)
    )
    channel = public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00")

    assert channel.title == "TEST FIXTURE Public Channel"
    assert channel.subscriber_count == 4321
    assert channel.view_count == 123456
    assert channel.metrics() == {
        "subscriber_count": 4321,
        "view_count": 123456,
        "video_count": 42,
    }
    # Everything the public API cannot see is named explicitly.
    unavailable = channel.unavailable_metrics()
    assert "estimated_revenue" in unavailable
    assert "click_through_rate" in unavailable
    assert "watch_time_minutes" in unavailable


@respx.mock
def test_a_hidden_subscriber_count_is_absent_and_listed_as_unavailable(api_key) -> None:
    payload = {"items": [dict(feeds.YOUTUBE_PUBLIC_CHANNEL["items"][0])]}
    payload["items"][0] = {
        **payload["items"][0],
        "statistics": {"hiddenSubscriberCount": True, "viewCount": "5", "videoCount": "1"},
    }
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=payload))

    channel = public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00")
    assert channel.subscriber_count is None
    assert channel.subscriber_count_hidden is True
    assert "subscriber_count" not in channel.metrics()
    assert "subscriber_count" in channel.unavailable_metrics()


@respx.mock
def test_a_genuine_zero_is_kept_as_zero(api_key) -> None:
    """0 subscribers with hiddenSubscriberCount=false is a measured fact, not unknown."""
    payload = {"items": [dict(feeds.YOUTUBE_PUBLIC_CHANNEL["items"][0])]}
    payload["items"][0] = {
        **payload["items"][0],
        "statistics": {
            "subscriberCount": "0",
            "hiddenSubscriberCount": False,
            "viewCount": "0",
            "videoCount": "0",
        },
    }
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=payload))

    channel = public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00")
    assert channel.subscriber_count == 0
    assert channel.metrics()["subscriber_count"] == 0
    assert "subscriber_count" not in channel.unavailable_metrics()


@respx.mock
def test_an_unknown_channel_is_a_clear_404(api_key) -> None:
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    with pytest.raises(NotFound) as exc:
        public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00")
    assert "Advanced settings" in exc.value.message


@respx.mock
def test_linking_a_public_channel_never_claims_oauth_consent(
    db: Session, channel: Channel, api_key, user
) -> None:
    """The critical separation: a public read grants nothing."""
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_PUBLIC_CHANNEL)
    )
    connection, public = public_service.link_public_channel(
        db, channel, "UCFIXTUREPUBLICCHANNEL00", user_id=user.id
    )
    db.commit()

    assert connection.public_channel_id == "UCFIXTUREPUBLICCHANNEL00"
    assert connection.public_verified_at is not None
    # Untouched: uploading still requires consent.
    assert connection.status == "not_connected"
    assert connection.access_token_encrypted is None
    assert connection.has_analytics_scope is False

    from nexora.services.youtube import oauth as oauth_service

    capabilities = oauth_service.connection_to_dict(connection)["capabilities"]
    assert capabilities["public_read"] is True
    assert capabilities["upload"] is False
    assert capabilities["revenue"] is False


@respx.mock
def test_linking_stores_an_incomplete_snapshot(
    db: Session, channel: Channel, api_key, user
) -> None:
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_PUBLIC_CHANNEL)
    )
    public_service.link_public_channel(db, channel, "UCFIXTUREPUBLICCHANNEL00", user_id=user.id)
    db.commit()

    snapshot = db.query(AnalyticsSnapshot).one()
    assert snapshot.source == "youtube_data_api_public"
    assert snapshot.is_complete is False, "public data is never the whole picture"
    assert snapshot.metrics["subscriber_count"] == 4321
    assert "estimated_revenue" in snapshot.unavailable_metrics


@respx.mock
def test_linking_is_audited_with_the_right_caveat(
    db: Session, channel: Channel, api_key, user
) -> None:
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_PUBLIC_CHANNEL)
    )
    public_service.link_public_channel(db, channel, "UCFIXTUREPUBLICCHANNEL00", user_id=user.id)
    db.commit()

    entry = db.query(AuditLog).filter(AuditLog.action == "youtube.public_channel_linked").one()
    assert "public read only" in (entry.summary or "")


def test_public_status_before_linking(db: Session, channel: Channel) -> None:
    status = public_service.public_status(db, channel)
    assert status["linked"] is False
    assert status["channel_id"] is None


@respx.mock
def test_the_scope_note_is_carried_in_the_payload(api_key) -> None:
    respx.get(CHANNELS_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_PUBLIC_CHANNEL)
    )
    payload = public_service.fetch_public_channel("UCFIXTUREPUBLICCHANNEL00").to_dict()
    assert "Public data only" in payload["scope_note"]
    assert "require an OAuth connection" in payload["scope_note"]
