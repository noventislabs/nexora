"""Signal Score — the channel-independent half of the Opportunity Score.

This is a **transparent, deterministic** score. It is not a prediction, and it is
deliberately not called a "probability of going viral" — nothing here can know that.
It ranks how workable a topic looks *given the evidence actually collected*.

It measures only what is true of the item itself: how fast it is moving, how crowded
the coverage is, how much material exists, how fresh it is, how durable its shape is,
and how reliable the source is. Audience relevance is deliberately **not** here,
because it is a property of a (trend, channel) pair rather than of the trend: one
normalized trend database serves a kids channel and a technology channel, and each
ranks it for itself in :mod:`nexora.services.trends.relevance`.

Rules that make it honest:

* A component is computed only when its inputs exist. A missing input yields
  ``value=None`` and that component's weight is dropped, not defaulted to a number.
* If fewer than :data:`MIN_AVAILABLE_WEIGHT` of the total weight can be computed, the
  score is ``None`` — the item is shown as "insufficient data", never as a low score.
* Every component records the ``basis`` string explaining exactly what it measured, so
  the number is always auditable in the UI.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexora.services.providers.trends.base import NormalizedTrend

#: Fraction of total weight that must be computable for a score to be produced.
MIN_AVAILABLE_WEIGHT = 0.5

#: Renormalized to sum to 1.0 after audience relevance moved out to the per-channel
#: engine. The relative ordering of the remaining components is unchanged.
WEIGHTS = {
    "trend_velocity": 0.28,
    "competition": 0.19,
    "content_availability": 0.16,
    "recency": 0.15,
    "evergreen_value": 0.12,
    "source_reliability": 0.10,
}

#: Engagement-per-hour that maps to a full velocity score, per source kind. These are
#: reference points for normalization, not claims about any particular channel.
VELOCITY_REFERENCE = {
    "youtube_data_api": 20000.0,
    "reddit": 400.0,
    "rss": 0.0,  # RSS exposes no engagement at all.
}

#: Markers that a story is tied to a moment and will date quickly.
TIME_BOUND_MARKERS = (
    "today", "breaking", "just announced", "live", "this week", "yesterday", "tonight",
    "right now", "update:", "leaked", "rumor", "rumour",
)
#: Markers of a durable, explainer-shaped topic.
EVERGREEN_MARKERS = (
    "how ", "why ", "what is", "explained", "guide", "history of", "the science of",
    "everything you need", "deep dive", "fundamentals", "principles",
)

_STOPWORDS = frozenset(
    """a an the and or but of for to in on at by with from as is are was were be been being
    this that these those it its his her their our your my we you they he she i new now how
    why what when where who which than then so if into over under about after before more
    most just very can will would could should may might do does did not no yes vs""".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")


@dataclass
class ScoreComponent:
    key: str
    label: str
    weight: float
    value: int | None
    basis: str

    @property
    def available(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "available": self.available, "rating": rating_for(self.value)}


@dataclass
class SignalScore:
    score: int | None
    components: list[ScoreComponent]
    available_weight: float
    unavailable_reason: str | None = None
    competition_level: str = "unknown"
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "available": self.score is not None,
            "unavailable_reason": self.unavailable_reason,
            "competition_level": self.competition_level,
            "available_weight": round(self.available_weight, 3),
            "components": [component.to_dict() for component in self.components],
            "method": (
                "Weighted mean of the components below, over only the components whose "
                "inputs were actually present. Missing inputs are dropped, never defaulted. "
                "This is the channel-independent signal; each channel's relevance is "
                "combined with it separately."
            ),
            "context": self.context,
        }


def rating_for(value: int | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= 75:
        return "High"
    if value >= 45:
        return "Medium"
    return "Low"


def token_sequence(text: str) -> list[str]:
    """Significant tokens in the order they appear."""
    return [
        token
        for token in _TOKEN_RE.findall((text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def tokenize(text: str) -> set[str]:
    return set(token_sequence(text))


#: A source needs at least this many items before a shared leading phrase can be
#: distinguished from coincidence.
MIN_ITEMS_FOR_PREFIX_DETECTION = 3


@dataclass
class ScanContext:
    """Batch-level facts used by components that need peers to compare against."""

    items: list[NormalizedTrend]
    token_sets: list[set[str]]
    source_kinds: list[str]
    #: Leading tokens stripped as per-source headline boilerplate, kept for explainability.
    boilerplate: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        items: list[NormalizedTrend],
        source_kinds: list[str],
        source_names: list[str] | None = None,
    ) -> ScanContext:
        """Build peer context, removing each source's standing headline prefix.

        Many feeds prefix every headline with the publication or section name
        ("Ars Technica: ...", "Channel Brief: ..."). Those tokens are shared by
        construction and say nothing about the topic, so counting them as overlap would
        manufacture competition that is not there. Only a *leading* phrase repeated
        across a source's own items is stripped — a genuinely dominant topic appears
        throughout the text, not always at the front, and is therefore preserved.
        """
        groups = source_names or source_kinds
        boilerplate = cls._headline_prefixes(items, groups)
        return cls(
            items=items,
            token_sets=[
                tokenize(f"{item.title} {item.summary or ''}") - boilerplate for item in items
            ],
            source_kinds=source_kinds,
            boilerplate=boilerplate,
        )

    @staticmethod
    def _headline_prefixes(items: list[NormalizedTrend], groups: list[str]) -> set[str]:
        by_group: dict[str, list[list[str]]] = {}
        for item, group in zip(items, groups, strict=False):
            by_group.setdefault(group, []).append(token_sequence(item.title))

        prefixes: set[str] = set()
        for sequences in by_group.values():
            if len(sequences) < MIN_ITEMS_FOR_PREFIX_DETECTION:
                continue
            shortest = min(len(sequence) for sequence in sequences)
            common: list[str] = []
            for position in range(shortest - 1):  # never strip an item's whole title
                token = sequences[0][position]
                if all(sequence[position] == token for sequence in sequences):
                    common.append(token)
                else:
                    break
            prefixes.update(common)
        return prefixes

    @property
    def size(self) -> int:
        return len(self.items)


def score_trend(
    trend: NormalizedTrend,
    *,
    source_kind: str,
    source_reliability: float,
    context: ScanContext | None = None,
    index: int | None = None,
    now: datetime | None = None,
) -> SignalScore:
    """Score one normalized trend's channel-independent signal.

    Never invents an input it does not have, and takes no channel: the result is a
    property of the item, reusable by every channel that can see it.
    """
    now = now or datetime.now(UTC)
    components = [
        _trend_velocity(trend, source_kind, now),
        _competition(trend, context, index),
        _content_availability(trend, context, index),
        _recency(trend, now),
        _evergreen_value(trend),
        _source_reliability(source_reliability),
    ]

    available = [component for component in components if component.available]
    available_weight = sum(component.weight for component in available)
    competition = next((c for c in components if c.key == "competition"), None)

    if available_weight < MIN_AVAILABLE_WEIGHT:
        missing = [component.label for component in components if not component.available]
        return SignalScore(
            score=None,
            components=components,
            available_weight=available_weight,
            unavailable_reason=(
                "Not enough signal to score this item. Missing inputs: " + ", ".join(missing) + "."
            ),
            competition_level=_competition_level(competition),
            context={"batch_size": context.size if context else 0},
        )

    weighted = sum((component.value or 0) * component.weight for component in available)
    return SignalScore(
        score=int(round(weighted / available_weight)),
        components=components,
        available_weight=available_weight,
        competition_level=_competition_level(competition),
        context={"batch_size": context.size if context else 0},
    )


def _competition_level(component: ScoreComponent | None) -> str:
    if component is None or component.value is None:
        return "unknown"
    # The component scores *opportunity*, so a high value means low competition.
    if component.value >= 70:
        return "low"
    if component.value >= 40:
        return "medium"
    return "high"


def _magnitude(trend: NormalizedTrend) -> tuple[float, str] | None:
    """The single engagement number this source actually provided, if any."""
    for key in ("views", "score"):
        value = trend.engagement.get(key)
        if isinstance(value, int | float) and value >= 0:
            return float(value), key
    return None


def _trend_velocity(trend: NormalizedTrend, source_kind: str, now: datetime) -> ScoreComponent:
    weight = WEIGHTS["trend_velocity"]
    reference = VELOCITY_REFERENCE.get(source_kind, 0.0)
    magnitude = _magnitude(trend)

    if reference <= 0:
        return ScoreComponent(
            "trend_velocity", "Trend velocity", weight, None,
            f"{source_kind} reports no engagement metrics, so velocity cannot be measured.",
        )
    if magnitude is None:
        return ScoreComponent(
            "trend_velocity", "Trend velocity", weight, None,
            "The source returned no engagement figure for this item.",
        )
    if trend.published_at is None:
        return ScoreComponent(
            "trend_velocity", "Trend velocity", weight, None,
            "No publication time was returned, so engagement cannot be divided by age.",
        )

    value, metric = magnitude
    age_hours = max((now - trend.published_at).total_seconds() / 3600.0, 1.0)
    per_hour = value / age_hours
    score = int(round(100 * math.log10(1 + per_hour) / math.log10(1 + reference)))
    return ScoreComponent(
        "trend_velocity", "Trend velocity", weight, max(0, min(100, score)),
        f"{per_hour:,.0f} {metric}/hour over {age_hours:,.0f}h, log-scaled against a "
        f"{reference:,.0f}/hour reference for {source_kind}.",
    )


#: Two items count as covering the same ground only if they share at least this many
#: distinctive tokens AND this share of the smaller item's distinctive vocabulary.
#: Batch-local boilerplate has already been removed by :class:`ScanContext`.
MIN_OVERLAP_TOKENS = 2
MIN_OVERLAP_RATIO = 0.25


def _peer_overlap(context: ScanContext | None, index: int | None) -> list[int] | None:
    """Indices of other scanned items that genuinely cover the same ground."""
    if context is None or index is None or context.size < 5:
        return None
    tokens = context.token_sets[index]
    if len(tokens) < 3:
        return None

    overlapping = []
    for other, other_tokens in enumerate(context.token_sets):
        if other == index or len(other_tokens) < 3:
            continue
        shared = len(tokens & other_tokens)
        if shared < MIN_OVERLAP_TOKENS:
            continue
        if shared / min(len(tokens), len(other_tokens)) < MIN_OVERLAP_RATIO:
            continue
        overlapping.append(other)
    return overlapping


def _competition(
    trend: NormalizedTrend, context: ScanContext | None, index: int | None
) -> ScoreComponent:
    weight = WEIGHTS["competition"]
    overlap = _peer_overlap(context, index)
    if overlap is None:
        return ScoreComponent(
            "competition", "Competition", weight, None,
            "Competition is measured against the other items in the same scan; this scan "
            "was too small (fewer than 5 items) to measure it.",
        )
    assert context is not None
    share = len(overlap) / max(context.size - 1, 1)
    score = int(round(100 * math.exp(-4 * share)))
    return ScoreComponent(
        "competition", "Competition", weight, max(0, min(100, score)),
        f"{len(overlap)} of {context.size - 1} other scanned items cover overlapping ground "
        f"({share:.0%}). Measures observed coverage in the configured sources only — not "
        "the whole of YouTube.",
    )


def _content_availability(
    trend: NormalizedTrend, context: ScanContext | None, index: int | None
) -> ScoreComponent:
    weight = WEIGHTS["content_availability"]
    overlap = _peer_overlap(context, index)
    if overlap is None:
        return ScoreComponent(
            "content_availability", "Research material", weight, None,
            "Corroborating coverage is measured within a scan of at least 5 items.",
        )
    assert context is not None
    distinct_sources = {context.source_kinds[other] for other in overlap}
    distinct_sources.add(context.source_kinds[index])
    has_summary = bool(trend.summary and len(trend.summary) > 120)

    # Independent corroboration matters more than raw volume.
    score = min(100, 25 * len(distinct_sources) + min(len(overlap), 5) * 6 + (15 if has_summary else 0))
    return ScoreComponent(
        "content_availability", "Research material", weight, score,
        f"{len(overlap)} corroborating item(s) across {len(distinct_sources)} source kind(s)"
        + ("; the item carries a substantive summary." if has_summary else "; no substantive summary."),
    )


def _recency(trend: NormalizedTrend, now: datetime) -> ScoreComponent:
    weight = WEIGHTS["recency"]
    if trend.published_at is None:
        return ScoreComponent(
            "recency", "Recency", weight, None, "The source returned no publication time."
        )
    hours = max((now - trend.published_at).total_seconds() / 3600.0, 0.0)
    # Half-life of three days.
    score = int(round(100 * math.pow(0.5, hours / 72.0)))
    return ScoreComponent(
        "recency", "Recency", weight, max(0, min(100, score)),
        f"Published {hours:,.0f}h ago; scored with a 72-hour half-life.",
    )


def _evergreen_value(trend: NormalizedTrend) -> ScoreComponent:
    weight = WEIGHTS["evergreen_value"]
    text = f"{trend.title} {trend.summary or ''}".lower()
    if not text.strip():
        return ScoreComponent(
            "evergreen_value", "Evergreen value", weight, None, "No text to assess."
        )
    time_bound = [marker for marker in TIME_BOUND_MARKERS if marker in text]
    evergreen = [marker for marker in EVERGREEN_MARKERS if marker in text]
    score = max(0, min(100, 55 + 15 * len(evergreen) - 18 * len(time_bound)))
    if evergreen and not time_bound:
        basis = f"Explainer-shaped phrasing ({', '.join(m.strip() for m in evergreen[:3])})."
    elif time_bound and not evergreen:
        basis = f"Tied to a moment ({', '.join(m.strip() for m in time_bound[:3])}), so it dates quickly."
    elif evergreen and time_bound:
        basis = "Mixes durable framing with time-bound language."
    else:
        basis = "Neutral phrasing: neither explicitly evergreen nor tied to a moment."
    return ScoreComponent("evergreen_value", "Evergreen value", weight, score, basis)


def _source_reliability(reliability: float) -> ScoreComponent:
    weight = WEIGHTS["source_reliability"]
    value = max(0, min(100, int(round(reliability * 100))))
    return ScoreComponent(
        "source_reliability", "Source reliability", weight, value,
        f"Configured reliability weight for this source ({reliability:.2f}).",
    )
