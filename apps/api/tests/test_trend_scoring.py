"""Signal Score: deterministic, transparent and never invented.

This is the channel-independent half of the Opportunity Score. Audience relevance
is deliberately not here — it is a property of a (trend, channel) pair and is
tested in ``test_channel_relevance.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexora.services.providers.trends.base import NormalizedTrend
from nexora.services.trends.scoring import (
    MIN_AVAILABLE_WEIGHT,
    WEIGHTS,
    ScanContext,
    rating_for,
    score_trend,
    tokenize,
)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def make(title: str, **kwargs) -> NormalizedTrend:
    return NormalizedTrend(external_id=kwargs.pop("external_id", title[:20]), title=title, **kwargs)


def score(trend: NormalizedTrend, *, kind="youtube_data_api", reliability=0.9, context=None, index=None):
    return score_trend(
        trend,
        source_kind=kind,
        source_reliability=reliability,
        context=context,
        index=index,
        now=NOW,
    )


def test_weights_sum_to_one() -> None:
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_scoring_is_deterministic() -> None:
    trend = make(
        "How AI chips reshape the semiconductor market",
        published_at=NOW - timedelta(hours=10),
        engagement={"views": 120000},
    )
    first, second = score(trend), score(trend)
    assert first.score == second.score
    assert [c.value for c in first.components] == [c.value for c in second.components]


def test_missing_inputs_are_dropped_not_defaulted() -> None:
    """No publication time and no engagement: those components are unavailable.

    The batch supplies the peer-comparison components, so enough weight remains to
    score the item honestly without substituting values for what is missing.
    """
    batch = [
        make("How AI chips reshape the semiconductor market"),
        make("Sourdough proofing technique"),
        make("Antarctic ice shelf measurements"),
        make("Municipal bond yield curves"),
        make("Volcanic ash aviation routing"),
    ]
    context = ScanContext.build(batch, ["rss"] * 5)
    result = score(batch[0], context=context, index=0)

    by_key = {component.key: component for component in result.components}
    assert by_key["trend_velocity"].value is None
    assert by_key["recency"].value is None
    assert "no engagement figure" in by_key["trend_velocity"].basis
    # Nothing was substituted for the missing inputs.
    assert by_key["trend_velocity"].to_dict()["available"] is False

    assert result.available_weight >= MIN_AVAILABLE_WEIGHT
    assert result.score is not None
    # The score is the mean over available components only, not diluted by zeros.
    expected = sum(
        (c.value or 0) * c.weight for c in result.components if c.value is not None
    ) / result.available_weight
    assert result.score == round(expected)


def test_an_isolated_item_with_no_signal_cannot_be_scored() -> None:
    """Outside a batch, too little is knowable — the score is withheld, not guessed."""
    result = score(make("How AI chips reshape the semiconductor market"))
    assert result.score is None
    assert result.available_weight < MIN_AVAILABLE_WEIGHT
    assert "Not enough signal" in (result.unavailable_reason or "")


def test_score_is_unavailable_when_too_little_is_known() -> None:
    """An RSS item with no timestamp and an unmatched topic cannot be scored honestly."""
    trend = make("x y")  # no tokens of substance, no timestamps, no engagement
    result = score_trend(trend, source_kind="rss", source_reliability=0.7, now=NOW)
    assert result.score is None
    assert result.available_weight < MIN_AVAILABLE_WEIGHT
    assert "Not enough signal" in (result.unavailable_reason or "")
    assert result.to_dict()["available"] is False


def test_rss_velocity_is_unavailable_because_rss_reports_no_engagement() -> None:
    trend = make("How solar storage economics work", published_at=NOW - timedelta(hours=5))
    result = score(trend, kind="rss", reliability=0.8)
    velocity = next(c for c in result.components if c.key == "trend_velocity")
    assert velocity.value is None
    assert "no engagement metrics" in velocity.basis


def test_velocity_rewards_faster_accumulation() -> None:
    fast = make("AI model release", published_at=NOW - timedelta(hours=2), engagement={"views": 200000})
    slow = make("AI model release", published_at=NOW - timedelta(hours=200), engagement={"views": 200000})
    fast_value = next(c for c in score(fast).components if c.key == "trend_velocity").value
    slow_value = next(c for c in score(slow).components if c.key == "trend_velocity").value
    assert fast_value is not None and slow_value is not None
    assert fast_value > slow_value


def test_recency_decays_with_a_72_hour_half_life() -> None:
    fresh = next(
        c for c in score(make("t", published_at=NOW)).components if c.key == "recency"
    ).value
    aged = next(
        c
        for c in score(make("t", published_at=NOW - timedelta(hours=72))).components
        if c.key == "recency"
    ).value
    assert fresh == 100
    assert aged == 50


def test_the_signal_score_is_the_same_whatever_channel_reads_it() -> None:
    """The score on a trend row must not depend on any channel.

    One normalized row serves a kids channel and a technology channel. If audience
    relevance were folded in here, the stored number would be true for at most one of
    them — so relevance lives on the per-channel record instead.
    """
    keys = {component.key for component in score(make("AI chips reshape manufacturing")).components}
    assert "audience_relevance" not in keys
    assert keys == set(WEIGHTS)

    # Nothing in the scoring signature can carry a channel.
    import inspect

    parameters = set(inspect.signature(score_trend).parameters)
    assert not any("channel" in name for name in parameters), parameters


def test_evergreen_penalises_time_bound_phrasing() -> None:
    explainer = score(make("Why semiconductor supply chains fail, explained"))
    breaking = score(make("BREAKING: chip plant fire, live updates today"))
    explainer_value = next(c for c in explainer.components if c.key == "evergreen_value").value
    breaking_value = next(c for c in breaking.components if c.key == "evergreen_value").value
    assert explainer_value is not None and breaking_value is not None
    assert explainer_value > breaking_value


def test_competition_is_unavailable_for_a_small_scan() -> None:
    trends = [make(f"AI chip story {i}") for i in range(3)]
    context = ScanContext.build(trends, ["rss"] * 3)
    result = score(trends[0], context=context, index=0)
    competition = next(c for c in result.components if c.key == "competition")
    assert competition.value is None
    assert "fewer than 5 items" in competition.basis
    assert result.competition_level == "unknown"


def test_competition_drops_when_many_peers_cover_the_same_ground() -> None:
    # Real coverage of one story varies its phrasing; only the topic words recur.
    crowded = [
        make("Artificial intelligence semiconductor market analysis"),
        make("Semiconductor supply constrains artificial intelligence growth"),
        make("Market analysis: semiconductor demand from artificial intelligence"),
        make("Why artificial intelligence reshaped semiconductor market forecasts"),
        make("Semiconductor makers respond to artificial intelligence demand"),
        make("Analysis of semiconductor pricing under artificial intelligence load"),
        make("Artificial intelligence workloads drive semiconductor capacity"),
        make("Semiconductor market shifts as artificial intelligence scales"),
    ]
    context = ScanContext.build(crowded, ["rss"] * 8)
    crowded_value = next(
        c for c in score(crowded[0], context=context, index=0).components if c.key == "competition"
    ).value

    varied = [
        make("Artificial intelligence semiconductor market analysis"),
        make("Sourdough bread proofing technique"),
        make("Antarctic ice shelf measurements"),
        make("Municipal bond yield curves"),
        make("Volcanic ash aviation routing"),
        make("Coral reef restoration funding"),
    ]
    varied_context = ScanContext.build(varied, ["rss"] * 6)
    varied_value = next(
        c
        for c in score(varied[0], context=varied_context, index=0).components
        if c.key == "competition"
    ).value

    assert crowded_value is not None and varied_value is not None
    assert varied_value > crowded_value


def test_corroboration_across_source_kinds_raises_research_material() -> None:
    items = [
        make("Artificial intelligence semiconductor market analysis"),
        make("Semiconductor supply constrains artificial intelligence growth"),
        make("Market analysis: semiconductor demand from artificial intelligence"),
        make("Why artificial intelligence reshaped semiconductor market forecasts"),
        make("Semiconductor makers respond to artificial intelligence demand"),
        make("Artificial intelligence workloads drive semiconductor capacity"),
    ]
    kinds = ["rss", "reddit", "youtube_data_api", "rss", "reddit", "rss"]
    single_kind = ScanContext.build(items, ["rss"] * 6, source_names=[f"s{i}" for i in range(6)])
    mixed_kind = ScanContext.build(items, kinds, source_names=[f"s{i}" for i in range(6)])

    single = next(
        c
        for c in score(items[0], context=single_kind, index=0).components
        if c.key == "content_availability"
    ).value
    mixed = next(
        c
        for c in score(items[0], context=mixed_kind, index=0).components
        if c.key == "content_availability"
    ).value
    assert single is not None and mixed is not None
    assert mixed > single


def test_every_component_explains_itself() -> None:
    result = score(make("AI chips", published_at=NOW, engagement={"views": 1000}))
    for component in result.components:
        assert component.basis, f"{component.key} has no basis string"
        assert component.to_dict()["rating"] in {"High", "Medium", "Low", "UNKNOWN"}


def test_score_output_never_claims_to_be_a_prediction() -> None:
    payload = score(make("AI chips", published_at=NOW, engagement={"views": 1000})).to_dict()
    text = str(payload).lower()
    for forbidden in ("viral", "probability", "guarantee", "will get", "predicted views"):
        assert forbidden not in text


def test_rating_labels() -> None:
    assert rating_for(None) == "UNKNOWN"
    assert rating_for(80) == "High"
    assert rating_for(50) == "Medium"
    assert rating_for(10) == "Low"


def test_tokenize_drops_stopwords_and_short_tokens() -> None:
    assert tokenize("The future of AI in the semiconductor market") == {
        "future", "semiconductor", "market",
    }


def test_shared_headline_boilerplate_is_not_mistaken_for_topical_overlap() -> None:
    """Feeds often prefix every headline; that must not read as competition.

    These five stories share the two-word prefix and nothing else. A pure shared-token
    count would call them all competitors; the ratio test correctly does not.
    """
    items = [
        make("Channel Brief: sourdough proofing humidity technique"),
        make("Channel Brief: antarctic ice shelf thickness measurements"),
        make("Channel Brief: municipal bond yield curve inversion"),
        make("Channel Brief: volcanic ash aviation rerouting policy"),
        make("Channel Brief: coral reef restoration grant funding"),
    ]
    context = ScanContext.build(items, ["rss"] * 5)
    competition = next(
        c for c in score(items[0], context=context, index=0).components if c.key == "competition"
    )
    assert competition.value is not None
    assert competition.value >= 90, "boilerplate-only overlap must not count as competition"
    assert "0 of 4" in competition.basis
    # The shared prefix was identified as boilerplate for this scan.
    assert {"channel", "brief"} <= context.boilerplate


def test_genuine_topical_overlap_still_registers() -> None:
    items = [
        make("Artificial intelligence semiconductor fabrication capacity expands"),
        make("Semiconductor fabrication capacity drives artificial intelligence costs"),
        make("Artificial intelligence fabrication semiconductor capacity constraints"),
        make("Municipal bond yield curve inversion"),
        make("Coral reef restoration grant funding"),
    ]
    context = ScanContext.build(items, ["rss"] * 5)
    competition = next(
        c for c in score(items[0], context=context, index=0).components if c.key == "competition"
    )
    assert competition.value is not None
    assert competition.value < 60, "real topical overlap must lower the competition score"
    assert "2 of 4" in competition.basis


def test_a_shared_prefix_is_stripped_but_a_shared_topic_is_not() -> None:
    """The discriminator: boilerplate leads every headline, a topic runs through them."""
    prefixed = [
        make("Daily Digest: sourdough proofing humidity"),
        make("Daily Digest: antarctic ice shelf thickness"),
        make("Daily Digest: municipal bond yield curves"),
    ]
    context = ScanContext.build(prefixed, ["rss"] * 3, source_names=["Digest"] * 3)
    assert context.boilerplate == {"daily", "digest"}

    varied = [
        make("Semiconductor demand rises"),
        make("Analysts revise semiconductor forecasts"),
        make("Why semiconductor pricing shifted"),
    ]
    varied_context = ScanContext.build(varied, ["rss"] * 3, source_names=["Wire"] * 3)
    assert varied_context.boilerplate == set(), "a recurring topic is not a headline prefix"
