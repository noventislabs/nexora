"""Channel-scoped analytics.

Every number here came from YouTube. Nothing is modelled, projected or interpolated,
and no comparison crosses a channel boundary — a kids channel's baseline says nothing
about a finance channel's, and mixing them would produce a comparison that means
nothing about either.

Three rules govern every statement this module produces:

1. **Observation, never causation.** "Received more views than this channel's median"
   is a measurement. "This topic performs well" is a claim about the future that
   nothing here can support.
2. **Always three numbers.** An observation period, a sample size and the baseline it
   was compared against. A comparison without them is not reportable, so
   :class:`Baseline` cannot be constructed without them.
3. **Insufficient data is an answer.** Below :data:`MIN_SAMPLE_SIZE` videos there is no
   baseline, and the honest output is ``INSUFFICIENT_DATA`` rather than a median of
   two videos presented as a channel norm.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ProviderUnavailable
from nexora.core.logging import get_logger
from nexora.db.models import (
    AnalyticsSnapshot,
    Channel,
    ContentPerformanceFeature,
    PerformanceObservation,
    YouTubeVideo,
)
from nexora.db.models.enums import AnalyticsScope
from nexora.services import audit

logger = get_logger(__name__)

#: Fewer videos than this and there is no channel norm to speak of. Reporting a median
#: of two would invite a decision the data cannot support.
MIN_SAMPLE_SIZE = 5

#: A video needs time to accumulate views before comparing it to anything.
MIN_VIDEO_AGE = timedelta(days=7)

#: Default window for a channel report.
DEFAULT_PERIOD = timedelta(days=28)

#: Metrics a comparison may be made on. Each is a count YouTube reported.
COMPARABLE_METRICS = ("views", "likes", "comments", "average_view_percentage", "click_through_rate")

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Baseline:
    """A channel's own historical norm for one metric.

    Construction requires the sample size and the observation period, because a
    baseline reported without them invites exactly the over-reading this product
    refuses.
    """

    metric: str
    median: float
    sample_size: int
    period_start: datetime
    period_end: datetime
    minimum: float
    maximum: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "median": self.median,
            "sample_size": self.sample_size,
            "observation_period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
                "days": (self.period_end - self.period_start).days,
            },
            "range": {"min": self.minimum, "max": self.maximum},
            "basis": (
                f"Median {self.metric.replace('_', ' ')} across {self.sample_size} videos "
                f"published by this channel between {self.period_start.date()} and "
                f"{self.period_end.date()}. This channel only."
            ),
        }


@dataclass
class BaselineResult:
    """Either a baseline, or a stated reason there is none."""

    baseline: Baseline | None
    status: str
    reason: str | None = None
    sample_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        if self.baseline is not None:
            return {"status": "AVAILABLE", **self.baseline.to_dict()}
        return {
            "status": self.status,
            "median": None,
            "sample_size": self.sample_size,
            "reason": self.reason,
        }


@dataclass
class Comparison:
    """One video measured against its own channel's baseline."""

    metric: str
    value: float
    baseline: Baseline
    observation: str
    possible_factors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.value / self.baseline.median if self.baseline.median else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "observation": self.observation,
            "compared_against": self.baseline.to_dict(),
            "possible_factors": self.possible_factors,
            "not_a_cause": (
                "These are attributes this video shares with others, not reasons it "
                "performed as it did. NEXORA cannot isolate a cause."
            ),
        }


# --------------------------------------------------------------------- collection
def collect_channel_snapshot(
    session: Session,
    channel: Channel,
    *,
    period_days: int = 28,
    user_id: uuid.UUID | None = None,
) -> AnalyticsSnapshot:
    """Read this channel's analytics from YouTube and store one snapshot.

    Requires the owner's OAuth consent with the analytics scope. Without it this
    raises rather than falling back to public data, because public data cannot supply
    impressions, click-through rate, watch time or revenue and a snapshot silently
    missing them would look like a channel with none.
    """
    from nexora.services.providers.analytics.youtube import YouTubeAnalyticsProvider
    from nexora.services.youtube import oauth as oauth_service

    connection = oauth_service.get_connection(session, channel.id)
    if not connection.has_analytics_scope:
        raise ProviderUnavailable(
            "This channel's YouTube connection does not include the analytics scope. "
            "Reconnect it and grant analytics access.",
            details={"missing_scope": "yt-analytics.readonly"},
        )
    if not connection.youtube_channel_id:
        raise ProviderUnavailable("This channel has no connected YouTube channel id.")

    token = oauth_service.access_token(session, connection)
    provider = YouTubeAnalyticsProvider()
    end = datetime.now(UTC)
    start = end - timedelta(days=period_days)

    result = provider.channel_metrics(
        token, channel_id=connection.youtube_channel_id, start=start, end=end
    )
    metrics = dict(result.metrics)
    unavailable = list(result.unavailable)

    # Revenue is a further consent. Absent it, the metric is absent — never zero.
    if connection.has_monetary_scope:
        revenue = provider.revenue_metrics(
            token, channel_id=connection.youtube_channel_id, start=start, end=end
        )
        if revenue is None:
            unavailable.append("estimated_revenue")
        else:
            metrics.update(revenue.metrics)
            unavailable.extend(revenue.unavailable)
    else:
        unavailable.append("estimated_revenue")

    snapshot = AnalyticsSnapshot(
        channel_id=channel.id,
        video_id=None,
        scope=AnalyticsScope.CHANNEL.value,
        source=result.source,
        period_start=start,
        period_end=end,
        captured_at=datetime.now(UTC),
        metrics=metrics,
        unavailable_metrics=sorted(set(unavailable)),
        raw=result.raw,
        # Complete only when nothing we asked for was withheld.
        is_complete=not unavailable,
    )
    session.add(snapshot)
    session.flush()

    audit.record(
        session,
        action="analytics.channel_snapshot",
        user_id=user_id,
        channel_id=channel.id,
        entity_type="analytics_snapshot",
        entity_id=snapshot.id,
        after={"metrics": sorted(metrics), "unavailable": snapshot.unavailable_metrics},
    )
    return snapshot


def collect_video_snapshot(
    session: Session,
    channel: Channel,
    video: YouTubeVideo,
    *,
    period_days: int = 28,
) -> AnalyticsSnapshot:
    """Read one video's analytics and store a snapshot plus its feature row."""
    from nexora.services.providers.analytics.youtube import YouTubeAnalyticsProvider
    from nexora.services.youtube import oauth as oauth_service

    connection = oauth_service.get_connection(session, channel.id)
    if not connection.has_analytics_scope or not connection.youtube_channel_id:
        raise ProviderUnavailable(
            "Reading per-video analytics requires this channel's OAuth connection with "
            "the analytics scope."
        )

    token = oauth_service.access_token(session, connection)
    end = datetime.now(UTC)
    start = end - timedelta(days=period_days)
    result = YouTubeAnalyticsProvider().video_metrics(
        token,
        channel_id=connection.youtube_channel_id,
        video_id=video.youtube_video_id,
        start=start,
        end=end,
    )

    snapshot = AnalyticsSnapshot(
        channel_id=channel.id,
        video_id=video.id,
        scope=AnalyticsScope.VIDEO.value,
        source=result.source,
        period_start=start,
        period_end=end,
        captured_at=datetime.now(UTC),
        metrics=dict(result.metrics),
        unavailable_metrics=list(result.unavailable),
        raw=result.raw,
        is_complete=not result.unavailable,
    )
    session.add(snapshot)
    session.flush()
    _upsert_features(session, channel, video, result.metrics)
    return snapshot


def _upsert_features(
    session: Session, channel: Channel, video: YouTubeVideo, metrics: dict[str, Any]
) -> ContentPerformanceFeature:
    """Store the video's attributes alongside its measurements.

    These are the inputs to "what do this channel's better-performing videos have in
    common" — a question about correlation within one channel, which is answerable,
    rather than about cause, which is not.
    """
    row = session.execute(
        select(ContentPerformanceFeature).where(
            ContentPerformanceFeature.video_id == video.id
        )
    ).scalar_one_or_none()
    if row is None:
        row = ContentPerformanceFeature(
            channel_id=channel.id, video_id=video.id, created_at=datetime.now(UTC)
        )
        session.add(row)

    if video.content_project_id is not None and row.topic_category is None:
        from nexora.db.models import ContentProject, TopicCandidate

        category = session.execute(
            select(TopicCandidate.category)
            .join(ContentProject, ContentProject.topic_candidate_id == TopicCandidate.id)
            .where(ContentProject.id == video.content_project_id)
        ).scalar_one_or_none()
        row.topic_category = category

    published = video.published_at
    row.duration_seconds = video.duration_seconds
    row.published_hour_local = published.hour if published else None
    row.published_weekday = published.weekday() if published else None
    row.title_length = len(video.title) if video.title else None
    row.title_has_number = any(char.isdigit() for char in video.title or "")
    row.title_has_question = "?" in (video.title or "")

    for key, attribute in (
        ("views", "views"),
        ("likes", "likes"),
        ("comments", "comments"),
        ("impressions", "impressions"),
        ("click_through_rate", "ctr_percent"),
        ("average_view_duration_seconds", "average_view_duration_seconds"),
        ("average_view_percentage", "average_view_percentage"),
    ):
        # Absent stays NULL. Writing 0 for "not reported" would corrupt every median
        # computed from this table afterwards.
        if key in metrics and metrics[key] is not None:
            setattr(row, attribute, metrics[key])

    session.flush()
    return row


# ---------------------------------------------------------------------- baselines
def channel_baseline(
    session: Session,
    channel: Channel,
    metric: str = "views",
    *,
    period_days: int = 90,
    exclude_video_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> BaselineResult:
    """This channel's own median for one metric, or a stated reason there is none.

    Scoped to one channel deliberately. A median across a user's channels would
    describe no audience that exists.
    """
    if metric not in COMPARABLE_METRICS:
        return BaselineResult(
            baseline=None,
            status=INSUFFICIENT_DATA,
            reason=f"'{metric}' is not a metric NEXORA compares against a baseline.",
        )

    now = now or datetime.now(UTC)
    start = now - timedelta(days=period_days)
    column = {
        "views": ContentPerformanceFeature.views,
        "likes": ContentPerformanceFeature.likes,
        "comments": ContentPerformanceFeature.comments,
        "average_view_percentage": ContentPerformanceFeature.average_view_percentage,
        "click_through_rate": ContentPerformanceFeature.ctr_percent,
    }[metric]

    conditions = [
        ContentPerformanceFeature.channel_id == channel.id,
        column.is_not(None),
        YouTubeVideo.published_at.is_not(None),
        YouTubeVideo.published_at >= start,
        # A video published yesterday has not had time to behave like the others.
        YouTubeVideo.published_at <= now - MIN_VIDEO_AGE,
    ]
    if exclude_video_id is not None:
        conditions.append(ContentPerformanceFeature.video_id != exclude_video_id)

    values = [
        float(value)
        for value in session.execute(
            select(column)
            .join(YouTubeVideo, YouTubeVideo.id == ContentPerformanceFeature.video_id)
            .where(*conditions)
        ).scalars()
        if value is not None
    ]

    if len(values) < MIN_SAMPLE_SIZE:
        return BaselineResult(
            baseline=None,
            status=INSUFFICIENT_DATA,
            sample_size=len(values),
            reason=(
                f"This channel has {len(values)} comparable video"
                f"{'' if len(values) == 1 else 's'} with a recorded {metric.replace('_', ' ')} "
                f"in the last {period_days} days. At least {MIN_SAMPLE_SIZE} are needed "
                "before a median means anything."
            ),
        )

    return BaselineResult(
        baseline=Baseline(
            metric=metric,
            median=float(statistics.median(values)),
            sample_size=len(values),
            period_start=start,
            period_end=now,
            minimum=min(values),
            maximum=max(values),
        ),
        status="AVAILABLE",
        sample_size=len(values),
    )


def compare_video_to_baseline(
    session: Session,
    channel: Channel,
    video: YouTubeVideo,
    metric: str = "views",
    *,
    period_days: int = 90,
) -> Comparison | BaselineResult:
    """Measure one video against its own channel's norm.

    The video is excluded from its own baseline: comparing it against a median it
    helped set would understate every difference.
    """
    features = session.execute(
        select(ContentPerformanceFeature).where(
            ContentPerformanceFeature.video_id == video.id
        )
    ).scalar_one_or_none()
    attribute = {
        "views": "views",
        "likes": "likes",
        "comments": "comments",
        "average_view_percentage": "average_view_percentage",
        "click_through_rate": "ctr_percent",
    }.get(metric)

    value = getattr(features, attribute, None) if features and attribute else None
    if value is None:
        return BaselineResult(
            baseline=None,
            status=INSUFFICIENT_DATA,
            reason=(
                f"No {metric.replace('_', ' ')} has been recorded for this video. "
                "Collect its analytics first."
            ),
        )

    result = channel_baseline(
        session, channel, metric, period_days=period_days, exclude_video_id=video.id
    )
    if result.baseline is None:
        return result

    baseline = result.baseline
    ratio = float(value) / baseline.median if baseline.median else 0.0
    direction = _describe(ratio)
    observation = (
        f"This video recorded {float(value):,.0f} {metric.replace('_', ' ')}, "
        f"{direction} this channel's median of {baseline.median:,.0f} across "
        f"{baseline.sample_size} videos published between "
        f"{baseline.period_start.date()} and {baseline.period_end.date()}."
    )

    return Comparison(
        metric=metric,
        value=float(value),
        baseline=baseline,
        observation=observation,
        possible_factors=_shared_attributes(session, channel, video, features),
    )


def _describe(ratio: float) -> str:
    """Wording that reports a measurement without implying a cause or a forecast."""
    if ratio >= 1.5:
        return "above"
    if ratio >= 1.1:
        return "somewhat above"
    if ratio >= 0.9:
        return "in line with"
    if ratio >= 0.5:
        return "somewhat below"
    return "below"


def _shared_attributes(
    session: Session,
    channel: Channel,
    video: YouTubeVideo,
    features: ContentPerformanceFeature | None,
) -> list[dict[str, Any]]:
    """Attributes this video shares with others on the same channel.

    Explicitly *possible contributing factors* and nothing stronger. These are
    correlations within one channel's small sample; they are not causes, and the
    wording never says they are.
    """
    if features is None:
        return []

    factors: list[dict[str, Any]] = []
    if features.published_weekday is not None:
        factors.append(
            {
                "attribute": "published_weekday",
                "value": features.published_weekday,
                "note": "Published on this weekday, in the channel's timezone.",
            }
        )
    if features.duration_seconds:
        factors.append(
            {
                "attribute": "duration_seconds",
                "value": features.duration_seconds,
                "note": f"Runtime {features.duration_seconds // 60} minutes.",
            }
        )
    if features.title_has_question:
        factors.append(
            {
                "attribute": "title_has_question",
                "value": True,
                "note": "The title is phrased as a question.",
            }
        )
    if features.title_has_number:
        factors.append(
            {
                "attribute": "title_has_number",
                "value": True,
                "note": "The title contains a number.",
            }
        )
    return factors


def record_observation(
    session: Session, channel: Channel, video: YouTubeVideo, comparison: Comparison
) -> PerformanceObservation:
    """Store a comparison as a durable observation."""
    row = PerformanceObservation(
        channel_id=channel.id,
        video_id=video.id,
        signal=comparison.metric,
        observation=comparison.observation,
        possible_factors=comparison.possible_factors,
        suggested_investigation=(
            "Compare this video's thumbnail, title and opening against others on this "
            "channel. NEXORA can measure the difference but cannot explain it."
        ),
        measured={
            "value": comparison.value,
            "baseline_median": comparison.baseline.median,
            "sample_size": comparison.baseline.sample_size,
            "observation_period_days": (
                comparison.baseline.period_end - comparison.baseline.period_start
            ).days,
        },
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


# ------------------------------------------------------------------- reporting
def latest_channel_snapshot(
    session: Session, channel_id: uuid.UUID
) -> AnalyticsSnapshot | None:
    return session.execute(
        select(AnalyticsSnapshot)
        .where(
            AnalyticsSnapshot.channel_id == channel_id,
            AnalyticsSnapshot.scope == AnalyticsScope.CHANNEL.value,
        )
        .order_by(AnalyticsSnapshot.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def snapshot_to_dict(snapshot: AnalyticsSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "scope": snapshot.scope,
        "source": snapshot.source,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "period": {
            "start": snapshot.period_start.isoformat() if snapshot.period_start else None,
            "end": snapshot.period_end.isoformat() if snapshot.period_end else None,
        },
        # Sparse by construction: an absent key means the metric was not reported.
        "metrics": snapshot.metrics or {},
        "unavailable_metrics": snapshot.unavailable_metrics or [],
        "is_complete": snapshot.is_complete,
        "note": (
            "Only metrics YouTube returned. A metric listed as unavailable was not "
            "reported or not permitted — it is not zero."
        ),
    }


def category_performance(
    session: Session, channel: Channel, *, period_days: int = 180
) -> dict[str, Any]:
    """Median views per topic category, **within this channel only**.

    A category with too few videos is reported as insufficient rather than given a
    median. The whole payload carries its observation period and per-category sample
    sizes, because a median without them is an invitation to over-read it.
    """
    now = datetime.now(UTC)
    start = now - timedelta(days=period_days)

    rows = session.execute(
        select(ContentPerformanceFeature.topic_category, ContentPerformanceFeature.views)
        .join(YouTubeVideo, YouTubeVideo.id == ContentPerformanceFeature.video_id)
        .where(
            ContentPerformanceFeature.channel_id == channel.id,
            ContentPerformanceFeature.views.is_not(None),
            ContentPerformanceFeature.topic_category.is_not(None),
            YouTubeVideo.published_at >= start,
            YouTubeVideo.published_at <= now - MIN_VIDEO_AGE,
        )
    ).all()

    grouped: dict[str, list[float]] = {}
    for category, views in rows:
        grouped.setdefault(str(category), []).append(float(views))

    categories = []
    for category, values in sorted(grouped.items()):
        if len(values) < MIN_SAMPLE_SIZE:
            categories.append(
                {
                    "category": category,
                    "status": INSUFFICIENT_DATA,
                    "median_views": None,
                    "sample_size": len(values),
                    "reason": (
                        f"{len(values)} video{'' if len(values) == 1 else 's'} — fewer than "
                        f"the {MIN_SAMPLE_SIZE} needed for a median."
                    ),
                }
            )
            continue
        categories.append(
            {
                "category": category,
                "status": "AVAILABLE",
                "median_views": float(statistics.median(values)),
                "sample_size": len(values),
            }
        )

    return {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "observation_period": {
            "start": start.isoformat(),
            "end": now.isoformat(),
            "days": period_days,
        },
        "categories": categories,
        "note": (
            "Medians within this channel only, across the period and sample sizes "
            "stated. These describe what this channel has already published; they do "
            "not predict how a future video will perform."
        ),
    }


def get_video(session: Session, channel_id: uuid.UUID, video_id: uuid.UUID) -> YouTubeVideo:
    video = session.get(YouTubeVideo, video_id)
    if video is None or video.channel_id != channel_id:
        raise NotFound("Video not found for this channel.")
    return video
