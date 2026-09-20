"""Channel profiles and the content-category vocabulary.

The theme: a channel's identity is configuration a person set, and NEXORA never fills
it in on their behalf.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, ValidationError
from nexora.db.models import Channel, ContentCategory, User
from nexora.services import categories as category_service
from nexora.services import channels as channel_service
from nexora.services import profiles as profile_service


# ------------------------------------------------------------------- vocabulary
def test_the_vocabulary_is_data_and_covers_more_than_business_and_technology(
    db: Session,
) -> None:
    keys = category_service.known_keys(db)

    # The audiences the product must support, not a business/tech-only list.
    assert {"kids", "family", "anime", "animation", "gaming", "entertainment", "music",
            "education", "history", "documentary", "commentary", "lifestyle", "health",
            "sports", "travel", "food", "diy", "news"} <= keys
    assert {"business", "finance", "technology", "ai", "science", "future"} <= keys
    assert len(keys) >= 25


def test_seeding_is_idempotent(db: Session) -> None:
    before = db.query(ContentCategory).count()
    assert category_service.seed_builtin_categories(db) == 0
    assert db.query(ContentCategory).count() == before


def test_a_channel_can_invent_a_category_the_seed_never_anticipated(
    db: Session, channel: Channel
) -> None:
    row = category_service.create_category(
        db,
        channel_id=channel.id,
        key="Retro Computing",
        label="Retro computing",
        keywords=["amiga", "commodore", "8-bit", "retro computing"],
    )
    db.commit()

    assert row.key == "retro_computing"
    assert row.is_builtin is False
    assert "retro_computing" in category_service.known_keys(db, channel.id)
    # …and it is invisible to a channel that did not define it.
    assert "retro_computing" not in category_service.known_keys(db)


def test_a_category_without_keywords_is_rejected(db: Session, channel: Channel) -> None:
    """A category that matches nothing would silently never fire."""
    with pytest.raises(ValidationError, match="at least one keyword"):
        category_service.create_category(
            db, channel_id=channel.id, key="empty", label="Empty", keywords=[]
        )


def test_a_channel_category_overrides_the_shared_one_of_the_same_key(
    db: Session, channel: Channel
) -> None:
    category_service.create_category(
        db,
        channel_id=channel.id,
        key="education",
        label="Education (our definition)",
        keywords=["phonics", "early years"],
    )
    db.commit()

    resolved = category_service.category_map(db, channel.id)["education"]
    assert resolved == ["phonics", "early years"]
    # Every other channel still sees the shared definition.
    assert "curriculum" in category_service.category_map(db)["education"]


def test_a_shared_category_cannot_be_edited_for_everyone(db: Session, channel: Channel) -> None:
    shared = next(
        row for row in category_service.list_categories(db) if row.key == "gaming"
    )
    with pytest.raises(ValidationError, match="shared category"):
        category_service.update_category(db, channel.id, shared.id, keywords=["nonsense"])


def test_a_duplicate_channel_category_is_a_conflict(db: Session, channel: Channel) -> None:
    category_service.create_category(
        db, channel_id=channel.id, key="custom", label="Custom", keywords=["thing"]
    )
    db.flush()
    with pytest.raises(Conflict):
        category_service.create_category(
            db, channel_id=channel.id, key="custom", label="Custom", keywords=["other"]
        )


def test_an_unknown_category_is_rejected_when_creating_a_channel(
    db: Session, user: User
) -> None:
    with pytest.raises(ValidationError, match="Unknown category"):
        channel_service.create_channel(
            db, user, name="Typo Channel", categories=["technolgy"]
        )


def test_a_kids_channel_can_be_created_from_the_vocabulary(db: Session, user: User) -> None:
    """The product must not be business-and-technology-shaped."""
    channel = channel_service.create_channel(
        db, user, name="Kiddo Tales", categories=["kids", "anime", "animation"]
    )
    db.commit()
    assert channel.categories == ["kids", "anime", "animation"]


# ---------------------------------------------------------------------- profile
def test_a_new_channel_has_an_undeclared_audience(db: Session, channel: Channel) -> None:
    profile = profile_service.get_profile(db, channel.id)

    assert profile.audience_classification is None
    assert profile.profile_completed_at is None
    payload = profile_service.profile_to_dict(channel, profile)
    assert payload["is_complete"] is False
    assert payload["incomplete_fields"] == ["audience_classification"]


def test_audience_is_never_inferred_from_the_channel_name_or_categories(
    db: Session, user: User
) -> None:
    """A channel called "Kiddo Anime Tales" in the kids category is still undeclared.

    Guessing here is how a made-for-kids declaration ends up wrong, so the code does
    not guess at all.
    """
    channel = channel_service.create_channel(
        db, user, name="Kiddo Anime Tales", categories=["kids", "anime"]
    )
    db.commit()

    profile = profile_service.get_profile(db, channel.id)
    assert profile.audience_classification is None

    from nexora.services.channels import get_channel_settings

    assert get_channel_settings(db, channel.id).made_for_kids_default is None


def test_declaring_the_audience_completes_the_profile(db: Session, channel: Channel) -> None:
    profile = profile_service.update_profile(db, channel, {"audience_classification": "kids"})
    db.commit()

    assert profile.audience_classification == "kids"
    assert profile.profile_completed_at is not None


def test_the_audience_declaration_does_not_touch_made_for_kids(
    db: Session, channel: Channel
) -> None:
    """Two separate declarations. One is editorial, the other is legal."""
    from nexora.services.channels import get_channel_settings

    profile_service.update_profile(db, channel, {"audience_classification": "kids"})
    db.commit()

    assert get_channel_settings(db, channel.id).made_for_kids_default is None


def test_the_audience_can_be_returned_to_undeclared(db: Session, channel: Channel) -> None:
    profile_service.update_profile(db, channel, {"audience_classification": "general"})
    profile = profile_service.update_profile(db, channel, {"audience_classification": None})
    db.commit()
    assert profile.audience_classification is None


def test_an_unrecognised_audience_classification_is_rejected(
    db: Session, channel: Channel
) -> None:
    with pytest.raises(ValidationError, match="not a recognised audience"):
        profile_service.update_profile(db, channel, {"audience_classification": "grown_ups"})


def test_a_topic_cannot_be_both_preferred_and_blocked(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError, match="both preferred and blocked"):
        profile_service.update_profile(
            db,
            channel,
            {"preferred_topics": ["robotics"], "blocked_topics": ["robotics"]},
        )


def test_a_category_cannot_be_both_primary_and_secondary(
    db: Session, user: User
) -> None:
    channel = channel_service.create_channel(db, user, name="Tech", categories=["technology"])
    db.flush()
    with pytest.raises(ValidationError, match="already a primary category"):
        profile_service.update_profile(db, channel, {"secondary_categories": ["technology"]})


def test_a_channel_must_allow_some_video_format(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError, match="at least one of short-form or long-form"):
        profile_service.update_profile(
            db, channel, {"short_form_enabled": False, "long_form_enabled": False}
        )


def test_phrases_are_kept_as_typed_not_stemmed_or_expanded(
    db: Session, channel: Channel
) -> None:
    """The operator must be able to predict what their own rule will do."""
    profile = profile_service.update_profile(
        db, channel, {"blocked_topics": ["  Political Campaign  ", "political campaign", ""]}
    )
    db.commit()
    assert profile.blocked_topics == ["political campaign"]


def test_languages_combine_the_channel_and_the_profile(db: Session, user: User) -> None:
    channel = channel_service.create_channel(
        db, user, name="Multi", categories=["news"], primary_language="en", secondary_language="bn"
    )
    db.flush()
    profile = profile_service.update_profile(db, channel, {"secondary_languages": ["hi", "en"]})
    db.commit()

    assert profile_service.languages_for(channel, profile) == ["en", "bn", "hi"]


def test_translation_is_off_by_default(db: Session, channel: Channel) -> None:
    assert profile_service.get_profile(db, channel.id).translation_enabled is False


def test_matching_inputs_report_whether_there_is_anything_to_match(
    db: Session, user: User
) -> None:
    bare = channel_service.create_channel(db, user, name="Bare", categories=["news"])
    db.flush()
    bare.categories = []
    profile = profile_service.get_profile(db, bare.id)
    assert profile_service.matching_inputs(bare, profile)["has_matchable_configuration"] is False

    bare.categories = ["news"]
    assert profile_service.matching_inputs(bare, profile)["has_matchable_configuration"] is True


# -------------------------------------------------------------------------- API
def test_profile_endpoints_require_authentication(client: TestClient) -> None:
    import uuid

    channel_id = uuid.uuid4()
    assert client.get(f"/api/channels/{channel_id}/profile").status_code == 401
    assert client.patch(f"/api/channels/{channel_id}/profile", json={}).status_code == 401
    assert client.get("/api/categories").status_code == 401


def test_another_users_profile_is_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Theirs")
    db.commit()

    assert auth_client.get(f"/api/channels/{other_channel.id}/profile").status_code == 403


def test_the_profile_api_reports_what_is_still_undeclared(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get(f"/api/channels/{channel.id}/profile").json()

    assert body["audience_classification"] is None
    assert body["is_complete"] is False
    assert "never derives the made-for-kids declaration" in body["note"]


def test_the_profile_api_updates_and_audits(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.db.models import AuditLog

    response = auth_client.patch(
        f"/api/channels/{channel.id}/profile",
        json={
            "audience_classification": "family",
            "audience_description": "Parents watching with young children.",
            "blocked_topics": ["violence"],
            "short_form_enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["audience_classification"] == "family"
    assert body["blocked_topics"] == ["violence"]
    assert body["is_complete"] is True

    actions = {row.action for row in db.query(AuditLog).all()}
    assert "channel.profile_updated" in actions


def test_profile_options_come_from_the_database(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get(f"/api/channels/{channel.id}/profile/options").json()

    keys = {item["key"] for item in body["categories"]}
    assert {"kids", "anime", "gaming", "business"} <= keys
    assert {item["value"] for item in body["audience_classifications"]} == {
        "kids", "family", "teen", "general", "mature"
    }
    assert "separate from YouTube's made-for-kids" in body["audience_note"]


def test_the_category_api_lists_the_vocabulary_and_says_it_is_extensible(
    auth_client: TestClient,
) -> None:
    body = auth_client.get("/api/categories").json()
    assert body["total"] >= 25
    assert "stored data, not a fixed list" in body["note"]


def test_creating_a_category_through_the_api_requires_csrf(
    auth_client: TestClient, channel: Channel
) -> None:
    from nexora.services import auth as auth_service

    auth_client.headers.pop(auth_service.CSRF_HEADER)
    response = auth_client.post(
        f"/api/categories?channel_id={channel.id}",
        json={"key": "x", "label": "X", "keywords": ["y"]},
    )
    assert response.status_code == 403
