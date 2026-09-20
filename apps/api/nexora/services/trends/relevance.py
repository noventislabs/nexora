"""Channel relevance: is this trend worth a video *for this channel*?

The same normalized trend database serves every channel a user owns. What differs is
the ranking, and this module is where that difference is computed — transparently.

Three rules keep it honest:

1. **Every verdict is explainable.** A match names the category and the keyword that
   fired. An exclusion names the rule the operator configured. The result is a trace a
   person can read and disagree with, not a number handed down.
2. **No invented audience.** Nothing here models demographics, psychographics or
   "what viewers like". It matches text against stored configuration. If the operator
   configured nothing, the answer is ``INSUFFICIENT_DATA``.
3. **No fabricated probability.** ``relevance_score`` is a weighted count of real
   matches on a 0–100 scale. It is not a likelihood of anything, and the code never
   calls it one.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import Channel, ChannelProfile, ChannelTopicRelevance, TrendingTopic
from nexora.db.models.enums import RelevanceStatus, ScoreStatus
from nexora.services.trends.scoring import tokenize

#: A primary category carries full weight; a secondary one is a genuine but weaker
#: signal. The operator chose which is which, so the weighting is theirs, not ours.
PRIMARY_WEIGHT = 1.0
SECONDARY_WEIGHT = 0.5
#: An explicit preferred topic is the strongest signal available: the operator typed
#: the phrase themselves.
PREFERENCE_WEIGHT = 1.5

#: Weighted match total that saturates the relevance scale. Chosen so that one solid
#: category match registers clearly while several push toward the top.
RELEVANCE_SATURATION = 3.0

#: Below this, an item is reported LOW_RELEVANCE rather than being offered as a topic.
LOW_RELEVANCE_THRESHOLD = 25

#: How much of the final channel score relevance accounts for. The remainder is the
#: channel-independent signal already computed on the trend row.
RELEVANCE_SHARE = 0.35


@dataclass
class RelevanceResult:
    """One (trend, channel) verdict, with the whole trace attached."""

    status: str
    relevance_score: int | None
    matched_categories: list[dict[str, Any]] = field(default_factory=list)
    matched_preferences: list[dict[str, Any]] = field(default_factory=list)
    excluded_by_rules: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[dict[str, str]] = field(default_factory=list)

    def reason(self, code: str, detail: str) -> None:
        self.reasons.append({"code": code, "detail": detail})

    @property
    def is_excluded(self) -> bool:
        return self.status == RelevanceStatus.EXCLUDED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "relevance_score": self.relevance_score,
            "matched_categories": self.matched_categories,
            "matched_preferences": self.matched_preferences,
            "excluded_by_rules": self.excluded_by_rules,
            "relevance_reasons": self.reasons,
        }


def _haystack(title: str, summary: str | None) -> str:
    return f"{title} {summary or ''}".lower()


@lru_cache(maxsize=4096)
def _pattern(phrase: str) -> re.Pattern[str]:
    # Word-boundary matching on the raw text rather than on a token set. A token set
    # drops short words, which would silently make "ai" — a category keyword and a
    # plausible blocked topic — impossible to match.
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def _phrase_hit(phrase: str, haystack: str) -> bool:
    """Match a phrase the way an operator would expect.

    Bounded on both sides, so blocking "war" does not also block "warranty" or
    "software", and a two-letter term like "ai" still matches the word "ai".
    """
    return _pattern(phrase).search(haystack) is not None


def evaluate(
    *,
    title: str,
    summary: str | None,
    item_language: str | None,
    item_category: str | None,
    channel_categories: list[str],
    profile_categories: list[str],
    preferred_topics: list[str],
    blocked_topics: list[str],
    content_exclusions: list[str],
    sensitive_restrictions: list[str],
    languages: list[str],
    translation_enabled: bool,
    category_keywords: dict[str, list[str]],
) -> RelevanceResult:
    """Match one item against one channel's configuration.

    Pure: it touches no database and no clock, which is what makes it directly
    testable and what makes the stored trace reproducible.
    """
    haystack = _haystack(title, summary)
    tokens = set(tokenize(haystack))
    result = RelevanceResult(status=RelevanceStatus.INSUFFICIENT_DATA.value, relevance_score=None)

    # --- Exclusions run first ---------------------------------------------------
    # An excluded item is never scored. Ranking something the operator has forbidden,
    # even at the bottom of the list, would misrepresent their instruction.
    for rule_name, phrases in (
        ("blocked_topics", blocked_topics),
        ("content_exclusions", content_exclusions),
        ("sensitive_content_restrictions", sensitive_restrictions),
    ):
        for phrase in phrases:
            if _phrase_hit(phrase, haystack):
                result.excluded_by_rules.append(
                    {
                        "rule": rule_name,
                        "value": phrase,
                        "detail": f"'{phrase}' appears in the item's title or summary.",
                    }
                )

    if result.excluded_by_rules:
        result.status = RelevanceStatus.EXCLUDED.value
        result.relevance_score = None
        names = ", ".join(sorted({rule["value"] for rule in result.excluded_by_rules}))
        result.reason(
            "excluded_by_channel_rule",
            f"Excluded by this channel's own rules: {names}.",
        )
        return result

    # --- Language ---------------------------------------------------------------
    if item_language and languages and item_language not in languages:
        if not translation_enabled:
            result.status = RelevanceStatus.EXCLUDED.value
            result.excluded_by_rules.append(
                {
                    "rule": "language",
                    "value": item_language,
                    "detail": (
                        f"The item is in '{item_language}'. This channel works in "
                        f"{', '.join(languages)} and translation is off."
                    ),
                }
            )
            result.reason(
                "language_mismatch",
                f"Item language '{item_language}' is not configured for this channel, and "
                "translation is disabled.",
            )
            return result
        result.reason(
            "translation_required",
            f"The item is in '{item_language}'; this channel has translation enabled.",
        )

    # --- Is there anything to match against at all? ------------------------------
    if not channel_categories and not profile_categories and not preferred_topics:
        result.reason(
            "no_matching_configuration",
            "This channel has no categories and no preferred topics configured, so there "
            "is nothing to match a trend against. Configure the channel profile.",
        )
        return result

    if not tokens:
        result.reason(
            "no_text_to_match",
            "The item has no usable title or summary text to compare against this "
            "channel's configuration.",
        )
        return result

    # --- Category matching ------------------------------------------------------
    weighted = 0.0
    for tier, keys, weight in (
        ("primary", channel_categories, PRIMARY_WEIGHT),
        ("secondary", profile_categories, SECONDARY_WEIGHT),
    ):
        for key in keys:
            keywords = category_keywords.get(key, [])
            hits = [word for word in keywords if _phrase_hit(word, haystack)]
            if not hits:
                continue
            result.matched_categories.append(
                {"category": key, "tier": tier, "keywords": hits[:6]}
            )
            # Extra hits inside one category add less than a second category would:
            # breadth of match is better evidence than one word repeated.
            weighted += weight * (1 + 0.25 * (len(hits) - 1))

    # The source's own category label counts, when the channel uses that category.
    if item_category and item_category in set(channel_categories) | set(profile_categories):
        tier = "primary" if item_category in channel_categories else "secondary"
        result.matched_categories.append(
            {"category": item_category, "tier": tier, "keywords": ["source category label"]}
        )
        weighted += PRIMARY_WEIGHT if tier == "primary" else SECONDARY_WEIGHT

    # --- Explicit preferences ----------------------------------------------------
    for phrase in preferred_topics:
        if _phrase_hit(phrase, haystack):
            result.matched_preferences.append({"preference": phrase, "matched": phrase})
            weighted += PREFERENCE_WEIGHT

    if weighted <= 0:
        result.status = RelevanceStatus.LOW_RELEVANCE.value
        result.relevance_score = 0
        configured = sorted(set(channel_categories) | set(profile_categories))
        result.reason(
            "no_category_match",
            "No meaningful match with this channel's configured content preferences"
            + (f" ({', '.join(configured)})" if configured else "")
            + ".",
        )
        return result

    score = min(100, int(round(100 * (1 - math.exp(-weighted / RELEVANCE_SATURATION)))))
    result.relevance_score = score
    result.status = (
        RelevanceStatus.RELEVANT.value
        if score >= LOW_RELEVANCE_THRESHOLD
        else RelevanceStatus.LOW_RELEVANCE.value
    )

    for entry in result.matched_categories:
        result.reason(
            "category_match",
            f"{entry['tier'].title()} category '{entry['category']}' matched: "
            + ", ".join(entry["keywords"]),
        )
    for entry in result.matched_preferences:
        result.reason(
            "preferred_topic_match",
            f"Matches the configured preferred topic '{entry['preference']}'.",
        )
    if result.status == RelevanceStatus.LOW_RELEVANCE.value:
        result.reason(
            "weak_match",
            f"Matched this channel's configuration only weakly (relevance {score}/100, "
            f"below the {LOW_RELEVANCE_THRESHOLD} threshold).",
        )
    return result


def evaluate_for_channel(
    session: Session,
    channel: Channel,
    profile: ChannelProfile,
    row: TrendingTopic,
    *,
    category_keywords: dict[str, list[str]],
) -> RelevanceResult:
    """Evaluate one stored trend row against one channel."""
    from nexora.services.profiles import languages_for

    return evaluate(
        title=row.title,
        summary=row.summary,
        item_language=row.language,
        item_category=row.category,
        channel_categories=list(channel.categories or []),
        profile_categories=list(profile.secondary_categories or []),
        preferred_topics=list(profile.preferred_topics or []),
        blocked_topics=list(profile.blocked_topics or []),
        content_exclusions=list(profile.content_exclusions or []),
        sensitive_restrictions=list(profile.sensitive_content_restrictions or []),
        languages=languages_for(channel, profile),
        translation_enabled=profile.translation_enabled,
        category_keywords=category_keywords,
    )


def combine_score(signal_score: int | None, relevance: RelevanceResult) -> tuple[int | None, str]:
    """The channel-specific Opportunity Score.

    Two real inputs, and **both are required**:

    * The signal says whether the story is moving, crowded and well-sourced. It says
      nothing about whether this channel should cover it.
    * Relevance says whether it fits this channel. It says nothing about whether the
      story is worth covering at all.

    Each is blind to what the other measures, so a score built on one of them would
    be a different quantity wearing this one's name. When either is unknown the
    result is ``None`` with a status saying which — never a number standing in for a
    missing one. A channel whose sources yield too little signal therefore sees
    "insufficient data" rather than a reassuring number built from keyword overlap.
    """
    if relevance.is_excluded:
        return None, ScoreStatus.EXCLUDED.value
    if relevance.relevance_score is None or signal_score is None:
        return None, ScoreStatus.INSUFFICIENT_DATA.value
    combined = (
        signal_score * (1 - RELEVANCE_SHARE) + relevance.relevance_score * RELEVANCE_SHARE
    )
    return int(round(combined)), ScoreStatus.SCORED.value


def store(
    session: Session,
    channel: Channel,
    row: TrendingTopic,
    relevance: RelevanceResult,
    *,
    now: datetime | None = None,
) -> ChannelTopicRelevance:
    """Upsert the (channel, trend) relevance record."""
    from nexora.services.trends.scan import freshness_of

    now = now or datetime.now(UTC)
    score, score_status = combine_score(row.signal_score, relevance)

    record = session.execute(
        select(ChannelTopicRelevance).where(
            ChannelTopicRelevance.channel_id == channel.id,
            ChannelTopicRelevance.trending_topic_id == row.id,
        )
    ).scalar_one_or_none()
    if record is None:
        record = ChannelTopicRelevance(channel_id=channel.id, trending_topic_id=row.id)
        session.add(record)

    record.status = relevance.status
    record.matched_categories = relevance.matched_categories
    record.matched_preferences = relevance.matched_preferences
    record.excluded_by_rules = relevance.excluded_by_rules
    record.relevance_reasons = relevance.reasons
    record.relevance_score = relevance.relevance_score
    record.score = score
    record.score_status = score_status
    record.score_breakdown = {
        "signal_score": row.signal_score,
        "signal_breakdown": row.signal_breakdown,
        "relevance_score": relevance.relevance_score,
        "relevance_share": RELEVANCE_SHARE,
        "note": (
            "A weighted combination of measured signal and configured relevance. It "
            "ranks workability given the evidence collected; it predicts nothing."
        ),
    }
    record.available_source_count = row.corroboration_count
    record.freshness = freshness_of(row, now=now)["state"]
    record.computed_at = now
    session.flush()
    return record


def recompute_for_channel(
    session: Session, channel: Channel, rows: list[TrendingTopic]
) -> list[ChannelTopicRelevance]:
    """Evaluate a batch of trend rows for one channel."""
    if not rows:
        return []
    from nexora.services import categories as category_service
    from nexora.services.profiles import get_profile

    profile = get_profile(session, channel.id)
    keywords = category_service.category_map(session, channel.id)
    now = datetime.now(UTC)

    records = []
    for row in rows:
        result = evaluate_for_channel(session, channel, profile, row, category_keywords=keywords)
        records.append(store(session, channel, row, result, now=now))
    return records


def relevance_to_dict(record: ChannelTopicRelevance) -> dict[str, Any]:
    return {
        "status": record.status,
        "relevance_score": record.relevance_score,
        "score": record.score,
        "score_status": record.score_status,
        "matched_categories": list(record.matched_categories or []),
        "matched_preferences": list(record.matched_preferences or []),
        "excluded_by_rules": list(record.excluded_by_rules or []),
        "relevance_reasons": list(record.relevance_reasons or []),
        "available_source_count": record.available_source_count,
        "freshness": record.freshness,
        "computed_at": record.computed_at.isoformat() if record.computed_at else None,
        "score_breakdown": record.score_breakdown,
    }


def visible_rows(
    session: Session, channel: Channel, user_id: uuid.UUID
) -> list[TrendingTopic]:
    """Trend rows this channel may rank: its own, plus the user's shared rows.

    Another channel's rows are never included. Cross-channel visibility would mix one
    audience's trends into another's ranking, which is the thing this design exists to
    prevent.
    """
    return list(
        session.execute(
            select(TrendingTopic).where(
                (TrendingTopic.channel_id == channel.id)
                | (
                    TrendingTopic.channel_id.is_(None)
                    & (TrendingTopic.user_id == user_id)
                )
            )
        ).scalars()
    )
