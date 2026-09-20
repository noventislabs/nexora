"""Channel relevance: the same trend, ranked differently for different channels.

These are the tests that hold the multi-channel promise. The central one is
``test_the_same_trend_ranks_differently_for_a_kids_and_a_technology_channel``: if that
passes, one normalized trend database genuinely serves several audiences.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nexora.db.models import Channel, ChannelTopicRelevance, TrendingTopic, TrendSource, User
from nexora.db.models.enums import RelevanceStatus, ScoreStatus, SourceScope
from nexora.services import categories as category_service
from nexora.services import channels as channel_service
from nexora.services import profiles as profile_service
from nexora.services.trends import relevance as relevance_service

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def keywords(db: Session) -> dict[str, list[str]]:
    return category_service.category_map(db)


def evaluate(keywords, title, *, summary=None, language=None, item_category=None, **overrides):
    defaults = {
        "channel_categories": [],
        "profile_categories": [],
        "preferred_topics": [],
        "blocked_topics": [],
        "content_exclusions": [],
        "sensitive_restrictions": [],
        "languages": ["en"],
        "translation_enabled": False,
    }
    defaults.update(overrides)
    return relevance_service.evaluate(
        title=title,
        summary=summary,
        item_language=language,
        item_category=item_category,
        category_keywords=keywords,
        **defaults,
    )


def make_source(db: Session, channel: Channel, *, scope=SourceScope.CHANNEL.value) -> TrendSource:
    source = TrendSource(
        channel_id=None if scope == SourceScope.SHARED.value else channel.id,
        user_id=channel.user_id,
        scope=scope,
        kind="rss",
        name=f"TEST FIXTURE feed {scope}",
        config={"url": "https://fixture.invalid/feed.xml"},
        reliability=0.8,
    )
    db.add(source)
    db.flush()
    return source


def make_trend(
    db: Session,
    source: TrendSource,
    channel: Channel | None,
    *,
    title: str,
    user: User,
    summary: str = "A synthetic fixture summary.",
    signal: int | None = 70,
    category: str | None = None,
    index: int = 0,
) -> TrendingTopic:
    row = TrendingTopic(
        source_id=source.id,
        channel_id=channel.id if channel else None,
        user_id=user.id,
        source_kind="rss",
        source_name=source.name,
        external_id=f"fixture-{index}",
        dedupe_hash=f"{index:064d}",
        content_hash=f"{index + 500:064d}",
        title=title,
        summary=summary,
        category=category,
        url=f"https://fixture.invalid/{index}",
        discovered_at=NOW - timedelta(hours=1),
        published_at=NOW - timedelta(hours=2),
        signal_score=signal,
        signal_breakdown={"score": signal, "components": []},
        scored_at=NOW,
        corroboration_count=2,
    )
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------------------------ the core promise
def test_the_same_trend_ranks_differently_for_a_kids_and_a_technology_channel(
    db: Session, user: User
) -> None:
    kids = channel_service.create_channel(db, user, name="Kids Tales", categories=["kids", "anime"])
    tech = channel_service.create_channel(
        db, user, name="Tech Desk", categories=["technology", "ai"]
    )
    db.flush()

    source = make_source(db, tech, scope=SourceScope.SHARED.value)
    row = make_trend(
        db,
        source,
        None,
        user=user,
        title="New AI video generation model released by a research lab",
        summary="The machine learning model generates video from text prompts.",
    )
    db.flush()

    relevance_service.recompute_for_channel(db, tech, [row])
    relevance_service.recompute_for_channel(db, kids, [row])
    db.commit()

    tech_record = db.query(ChannelTopicRelevance).filter_by(channel_id=tech.id).one()
    kids_record = db.query(ChannelTopicRelevance).filter_by(channel_id=kids.id).one()

    assert tech_record.status == RelevanceStatus.RELEVANT.value
    assert {entry["category"] for entry in tech_record.matched_categories} >= {"ai"}
    assert tech_record.score is not None

    assert kids_record.status == RelevanceStatus.LOW_RELEVANCE.value
    assert kids_record.matched_categories == []
    reasons = " ".join(entry["detail"] for entry in kids_record.relevance_reasons)
    assert "No meaningful match" in reasons
    assert "kids" in reasons

    # One row, two verdicts, and neither channel's ranking leaked into the other's.
    assert tech_record.trending_topic_id == kids_record.trending_topic_id


def test_a_channel_never_sees_another_channels_trend_rows(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()

    private_source = make_source(db, first)
    private_row = make_trend(
        db, private_source, first, user=user, title="A story only the first channel collected"
    )
    db.commit()

    visible = relevance_service.visible_rows(db, second, user.id)
    assert private_row not in visible
    assert relevance_service.visible_rows(db, first, user.id) == [private_row]


def test_a_shared_row_is_visible_to_every_channel_the_user_owns(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["kids"])
    db.flush()

    shared_source = make_source(db, first, scope=SourceScope.SHARED.value)
    shared_row = make_trend(
        db, shared_source, None, user=user, title="A story from the shared feed"
    )
    db.commit()

    assert shared_row in relevance_service.visible_rows(db, first, user.id)
    assert shared_row in relevance_service.visible_rows(db, second, user.id)


def test_another_users_shared_rows_are_not_visible(db: Session, user: User) -> None:
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    mine = channel_service.create_channel(db, user, name="Mine", categories=["technology"])
    other_user = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    theirs = channel_service.create_channel(db, other_user, name="Theirs", categories=["technology"])
    db.flush()

    their_source = make_source(db, theirs, scope=SourceScope.SHARED.value)
    their_row = make_trend(db, their_source, None, user=other_user, title="Their shared story")
    db.commit()

    assert their_row not in relevance_service.visible_rows(db, mine, user.id)


# ------------------------------------------------------------------------ exclusions
def test_a_blocked_topic_excludes_rather_than_ranking_low(keywords) -> None:
    """An operator's exclusion is an instruction, not a ranking hint."""
    result = evaluate(
        keywords,
        "Gambling sponsorship deal signed by a major football club",
        channel_categories=["sports"],
        blocked_topics=["gambling"],
    )

    assert result.status == RelevanceStatus.EXCLUDED.value
    assert result.relevance_score is None
    assert result.excluded_by_rules[0]["rule"] == "blocked_topics"
    assert result.excluded_by_rules[0]["value"] == "gambling"
    # It matched sports too — the exclusion still wins, and no score is produced.
    assert result.matched_categories == []


def test_an_excluded_item_gets_no_score_at_all(keywords) -> None:
    result = evaluate(
        keywords, "A story about gambling", channel_categories=["news"], blocked_topics=["gambling"]
    )
    score, status = relevance_service.combine_score(90, result)
    assert score is None
    assert status == ScoreStatus.EXCLUDED.value


def test_a_single_word_block_does_not_match_inside_a_longer_word(keywords) -> None:
    """Blocking 'war' must not also block 'warranty' or 'software'."""
    result = evaluate(
        keywords,
        "Extended warranty terms for new software releases",
        channel_categories=["technology"],
        blocked_topics=["war"],
    )
    assert result.status != RelevanceStatus.EXCLUDED.value


def test_sensitive_restrictions_and_content_exclusions_are_reported_by_name(keywords) -> None:
    result = evaluate(
        keywords,
        "Graphic violence in a new release sparks debate",
        channel_categories=["entertainment"],
        sensitive_restrictions=["violence"],
        content_exclusions=["debate"],
    )
    rules = {entry["rule"] for entry in result.excluded_by_rules}
    assert rules == {"sensitive_content_restrictions", "content_exclusions"}


# -------------------------------------------------------------------- insufficient
def test_a_channel_with_no_configuration_gets_insufficient_data_not_a_low_score(
    keywords,
) -> None:
    result = evaluate(keywords, "A perfectly ordinary news story about technology")

    assert result.status == RelevanceStatus.INSUFFICIENT_DATA.value
    assert result.relevance_score is None
    assert result.reasons[0]["code"] == "no_matching_configuration"
    assert "Configure the channel profile" in result.reasons[0]["detail"]


def test_insufficient_relevance_yields_no_score_even_with_a_strong_signal(keywords) -> None:
    result = evaluate(keywords, "A story", channel_categories=[])
    score, status = relevance_service.combine_score(95, result)
    assert score is None
    assert status == ScoreStatus.INSUFFICIENT_DATA.value


def test_a_missing_signal_yields_no_score_even_with_strong_relevance(keywords) -> None:
    result = evaluate(
        keywords, "Machine learning model training costs", channel_categories=["ai"]
    )
    assert result.status == RelevanceStatus.RELEVANT.value
    score, status = relevance_service.combine_score(None, result)
    assert score is None
    assert status == ScoreStatus.INSUFFICIENT_DATA.value


def test_an_item_with_no_text_is_insufficient_data(keywords) -> None:
    result = evaluate(keywords, "x y", channel_categories=["technology"])
    assert result.status == RelevanceStatus.INSUFFICIENT_DATA.value
    assert result.reasons[0]["code"] == "no_text_to_match"


# ---------------------------------------------------------------------- explanation
def test_every_match_names_the_keyword_that_fired(keywords) -> None:
    result = evaluate(
        keywords,
        "Anime studio announces a new manga adaptation",
        channel_categories=["anime"],
    )

    assert result.status == RelevanceStatus.RELEVANT.value
    entry = next(item for item in result.matched_categories if item["category"] == "anime")
    assert "anime" in entry["keywords"]
    assert entry["tier"] == "primary"
    assert any(reason["code"] == "category_match" for reason in result.reasons)


def test_a_preferred_topic_is_reported_separately_from_a_category(keywords) -> None:
    result = evaluate(
        keywords,
        "Semiconductor export rules tighten again",
        channel_categories=["technology"],
        preferred_topics=["export rules"],
    )
    assert result.matched_preferences == [
        {"preference": "export rules", "matched": "export rules"}
    ]
    assert any(reason["code"] == "preferred_topic_match" for reason in result.reasons)


def test_a_secondary_category_counts_less_than_a_primary_one(keywords) -> None:
    primary = evaluate(keywords, "Gaming console launch", channel_categories=["gaming"])
    secondary = evaluate(keywords, "Gaming console launch", profile_categories=["gaming"])
    assert primary.relevance_score > secondary.relevance_score


def test_no_output_field_claims_to_predict_anything(keywords) -> None:
    result = evaluate(
        keywords, "Machine learning model training costs", channel_categories=["ai"]
    )
    text = " ".join(
        [reason["detail"] for reason in result.reasons]
        + [str(result.to_dict())]
    ).lower()
    for phrase in ("will go viral", "probability of", "predicted views", "expected revenue",
                   "likely to succeed", "guaranteed"):
        assert phrase not in text, phrase


# ------------------------------------------------------------------------- language
def test_an_off_language_item_is_excluded_when_translation_is_off(keywords) -> None:
    result = evaluate(
        keywords,
        "Machine learning model released",
        language="fr",
        channel_categories=["ai"],
        languages=["en"],
        translation_enabled=False,
    )
    assert result.status == RelevanceStatus.EXCLUDED.value
    assert result.excluded_by_rules[0]["rule"] == "language"


def test_an_off_language_item_is_kept_when_translation_is_on(keywords) -> None:
    result = evaluate(
        keywords,
        "Machine learning model released",
        language="fr",
        channel_categories=["ai"],
        languages=["en"],
        translation_enabled=True,
    )
    assert result.status == RelevanceStatus.RELEVANT.value
    assert any(reason["code"] == "translation_required" for reason in result.reasons)


def test_a_secondary_language_is_accepted_without_translation(keywords) -> None:
    result = evaluate(
        keywords,
        "Machine learning model released",
        language="bn",
        channel_categories=["ai"],
        languages=["en", "bn"],
    )
    assert result.status == RelevanceStatus.RELEVANT.value


# ------------------------------------------------------------------------- storage
def test_the_stored_record_carries_the_whole_explanation(db: Session, user: User) -> None:
    channel = channel_service.create_channel(db, user, name="Tech", categories=["technology"])
    db.flush()
    source = make_source(db, channel)
    row = make_trend(
        db, source, channel, user=user, title="Semiconductor cloud platform launched"
    )
    db.flush()

    records = relevance_service.recompute_for_channel(db, channel, [row])
    db.commit()

    record = records[0]
    assert record.status == RelevanceStatus.RELEVANT.value
    assert record.score_status == ScoreStatus.SCORED.value
    assert record.score is not None
    assert record.matched_categories
    assert record.relevance_reasons
    # Counted, never estimated.
    assert record.available_source_count == row.corroboration_count
    assert record.score_breakdown["signal_score"] == row.signal_score
    assert "predicts nothing" in record.score_breakdown["note"]


def test_recomputing_after_a_profile_change_updates_the_verdict(
    db: Session, user: User
) -> None:
    """Relevance is stored, so editing a profile must visibly re-rank."""
    channel = channel_service.create_channel(db, user, name="Tech", categories=["technology"])
    db.flush()
    source = make_source(db, channel)
    row = make_trend(db, source, channel, user=user, title="Cloud platform launched for developers")
    db.flush()

    before = relevance_service.recompute_for_channel(db, channel, [row])[0]
    assert before.status == RelevanceStatus.RELEVANT.value

    profile_service.update_profile(db, channel, {"blocked_topics": ["cloud"]})
    after = relevance_service.recompute_for_channel(db, channel, [row])[0]
    db.commit()

    assert after.id == before.id, "the record is updated in place, not duplicated"
    assert after.status == RelevanceStatus.EXCLUDED.value
    assert after.score is None


def test_a_channel_specific_score_sits_between_its_two_inputs(db: Session, user: User) -> None:
    channel = channel_service.create_channel(db, user, name="Tech", categories=["technology"])
    db.flush()
    source = make_source(db, channel)
    row = make_trend(
        db, source, channel, user=user, title="Semiconductor cloud platform launched", signal=80
    )
    db.flush()
    record = relevance_service.recompute_for_channel(db, channel, [row])[0]
    db.commit()

    low, high = sorted((row.signal_score, record.relevance_score))
    assert low <= record.score <= high
