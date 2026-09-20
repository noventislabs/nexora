"""Publishing gates, authorization, idempotency and verification.

These are the safety-critical tests: they assert that a video cannot be published when
a gate fails, cannot be published twice, and that autopilot cannot publish itself past
a block.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import (
    Conflict,
    ProviderUnavailable,
    SafetyBlocked,
    UpstreamPermanentError,
)
from nexora.db.models import (
    AuditLog,
    Channel,
    ContentProject,
    ContentScript,
    FactCheck,
    MetadataVersion,
    PublishJob,
    ScriptVersion,
    User,
    VideoProject,
    VideoRenderJob,
    VoiceJob,
    YouTubeConnection,
    YouTubeVideo,
)
from nexora.db.models.enums import CheckStatus, PublishAuthorization, RunStatus
from nexora.services import assets as asset_service
from nexora.services import publishing as publishing_service
from nexora.services.channels import get_automation_settings, get_channel_settings
from tests.fixtures import feeds
from tests.media_helpers import ffmpeg_available

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SESSION_URL = "https://upload.googleapis.com/fixture-session"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg is not installed")


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "fixture-client-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "fixture-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


@pytest.fixture
def publishable(db: Session, channel: Channel, user: User, oauth_configured):
    """A project with every artefact and gate satisfied, ready to publish."""
    from nexora.core.crypto import encrypt_str

    # Declare made-for-kids explicitly; publishing is blocked while it is undecided.
    settings_row = get_channel_settings(db, channel.id)
    settings_row.made_for_kids_default = False
    settings_row.youtube_category_id = "28"

    from nexora.db.models import ResearchDocument, TopicCandidate, TopicResearch

    candidate = TopicCandidate(
        channel_id=channel.id, title="Why the numbers disagree", angle="Explains it.",
        status="converted", sources=[],
    )
    db.add(candidate)
    db.flush()
    research = TopicResearch(
        topic_candidate_id=candidate.id,
        channel_id=channel.id,
        status="SUCCESS",
        key_facts=[],
        claims=[],
        document_count=1,
    )
    db.add(research)
    db.flush()
    db.add(
        ResearchDocument(
            research_id=research.id,
            channel_id=channel.id,
            origin="trend_item",
            title="TEST FIXTURE source",
            publisher="Fixture Press",
            text="The regional foundry added forty thousand wafer starts in the quarter.",
            word_count=11,
            fetch_decision="DISABLED",
            created_at=datetime.now(UTC),
        )
    )
    db.flush()

    project = ContentProject(
        channel_id=channel.id,
        title="Why the numbers disagree",
        research_id=research.id,
        status="ready",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
        approval_status="approved",
        approved_by=user.id,
        approved_at=datetime.now(UTC),
    )
    db.add(project)
    db.flush()

    script = ContentScript(content_project_id=project.id, current_version=1)
    db.add(script)
    db.flush()
    version = ScriptVersion(
        script_id=script.id,
        version=1,
        sections=[{"kind": "hook", "heading": "Opening", "narration": "Words.", "document_indices": []}],
        plain_text="Words.",
        narration_text="Two bodies counted the same quarter and disagreed about the result.",
        word_count=11,
        estimated_duration_seconds=480,
        created_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    project.current_script_version_id = version.id

    # Real MP4 magic bytes, so content validation and checksumming are genuine.
    render_asset = asset_service.store_bytes(
        db,
        channel,
        kind="render",
        data=b"\x00\x00\x00 ftypisom" + b"\x00" * 4096,
        content_project_id=project.id,
        source="system",
        license_type="system_generated",
        verify_media=True,
    )
    project.current_render_asset_id = render_asset.id

    video_project = VideoProject(content_project_id=project.id, status="rendered")
    db.add(video_project)
    db.flush()
    db.add(
        VideoRenderJob(
            video_project_id=video_project.id,
            status=RunStatus.SUCCESS.value,
            output_asset_id=render_asset.id,
            duration_seconds=470.0,
            resolution="1920x1080",
            output_bytes=render_asset.size_bytes,
            ffmpeg_version="6.1.1",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )
    db.add(
        VoiceJob(
            content_project_id=project.id,
            script_version_id=version.id,
            provider="elevenlabs",
            status=RunStatus.SUCCESS.value,
            duration_seconds=470.0,
            timing_source="provider",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )
    db.add(
        FactCheck(
            content_project_id=project.id,
            script_version_id=version.id,
            status=CheckStatus.PASS.value,
            supported_count=5,
            claims=[],
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        MetadataVersion(
            content_project_id=project.id,
            version=1,
            title="Why two bodies counted the same quarter differently",
            description="A description.",
            tags=["semiconductor"],
            category_id="28",
            default_language="en",
            made_for_kids=False,
            created_at=datetime.now(UTC),
        )
    )

    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = "connected"
    connection.youtube_channel_id = "UCFIXTURECHANNELID000000"
    connection.youtube_channel_title = "TEST FIXTURE Channel"
    connection.access_token_encrypted = encrypt_str("fixture-access-token")
    connection.refresh_token_encrypted = encrypt_str("fixture-refresh-token")
    connection.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    connection.scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    db.commit()
    return project


# --------------------------------------------------------------------- preflight
def test_a_ready_project_passes_every_gate(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert result.can_publish, f"unexpected blockers: {result.blockers}"
    keys = {gate["key"] for gate in result.gates}
    assert {"emergency_stop", "render", "metadata", "quality_check", "copyright_check",
            "youtube_connection", "daily_limit"} <= keys


def test_an_undeclared_made_for_kids_blocks_publishing(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    """The declaration carries legal weight, so NEXORA refuses to guess it."""
    settings_row = get_channel_settings(db, channel.id)
    settings_row.made_for_kids_default = None
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish
    assert any("Quality check" in blocker for blocker in result.blockers)

    from nexora.services import quality as quality_service

    check = quality_service.latest_quality_check(db, publishable.id)
    declaration = next(c for c in check.checks if c["key"] == "made_for_kids_declared")
    assert declaration["passed"] is False
    assert "will not guess" in declaration["detail"]


def test_the_emergency_stop_blocks_publishing(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    automation = get_automation_settings(db, channel.id)
    automation.emergency_stop = True
    automation.emergency_stop_reason = "Operator halted publishing"
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish
    assert any("Emergency stop" in blocker for blocker in result.blockers)

    with pytest.raises(SafetyBlocked):
        publishing_service.create_publish_job(
            db, channel, publishable, authorized_by=PublishAuthorization.USER
        )
    assert db.query(PublishJob).count() == 0


def test_an_unknown_licence_asset_blocks_publishing(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    asset_service.store_bytes(
        db,
        channel,
        kind="image",
        data=feeds.TINY_PNG,
        content_project_id=publishable.id,
        # No licence declared: defaults to LICENSE UNKNOWN.
    )
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish
    assert any("Copyright check" in blocker for blocker in result.blockers)


def test_a_failing_fact_check_blocks_publishing(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    check = db.query(FactCheck).one()
    check.status = CheckStatus.FAIL.value
    check.unsupported_count = 2
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish


def test_the_daily_limit_blocks_publishing(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    automation = get_automation_settings(db, channel.id)
    assert automation.max_videos_per_day == 1
    db.add(
        PublishJob(
            channel_id=channel.id,
            content_project_id=publishable.id,
            idempotency_key="already-published",
            status=RunStatus.SUCCESS.value,
            privacy_status="public",
            finished_at=datetime.now(UTC) - timedelta(hours=2),
            youtube_video_id="EARLIERVIDEO",
        )
    )
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish
    assert any("Videos per day" in blocker for blocker in result.blockers)


def test_a_schedule_outside_the_publishing_window_is_blocked(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    import zoneinfo

    automation = get_automation_settings(db, channel.id)
    automation.publish_window_start_hour = 8
    automation.publish_window_end_hour = 22
    db.commit()

    zone = zoneinfo.ZoneInfo(automation.timezone)
    at_three_am = datetime.now(zone).replace(hour=3, minute=0) + timedelta(days=1)

    result = publishing_service.run_preflight(
        db,
        channel,
        publishable,
        authorized_by=PublishAuthorization.USER,
        scheduled_for=at_three_am,
    )
    assert not result.can_publish
    assert any("Publishing window" in blocker for blocker in result.blockers)


# ----------------------------------------------------------------- authorization
def test_autopilot_cannot_publish_while_autopilot_is_off(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    automation = get_automation_settings(db, channel.id)
    assert automation.autopilot_enabled is False

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.AUTOPILOT
    )
    assert not result.can_publish
    assert any("Autopilot" in blocker for blocker in result.blockers)


def test_autopilot_cannot_publish_while_approval_is_required_and_absent(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    automation = get_automation_settings(db, channel.id)
    automation.autopilot_enabled = True
    automation.require_human_approval = False
    automation.auto_publish_enabled = True
    automation.require_human_approval = True
    publishable.approval_status = "pending"
    db.commit()

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.AUTOPILOT
    )
    assert not result.can_publish
    assert any("Human approval" in blocker for blocker in result.blockers)


def test_autopilot_may_never_force_past_a_block(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    """An override is a human decision. Automation cannot grant it to itself."""
    with pytest.raises(SafetyBlocked) as exc:
        publishing_service.create_publish_job(
            db,
            channel,
            publishable,
            authorized_by=PublishAuthorization.AUTOPILOT,
            force=True,
        )
    assert "may not override" in exc.value.message
    assert db.query(PublishJob).count() == 0


def test_an_operator_may_force_past_a_block_and_it_is_audited(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    automation = get_automation_settings(db, channel.id)
    automation.emergency_stop = True
    db.commit()

    job = publishing_service.create_publish_job(
        db,
        channel,
        publishable,
        authorized_by=PublishAuthorization.USER,
        user_id=user.id,
        force=True,
    )
    db.commit()

    assert job.status == RunStatus.QUEUED.value
    entry = db.query(AuditLog).filter(AuditLog.action == "publish.authorized").one()
    assert "FORCED past" in (entry.summary or "")


# ------------------------------------------------------------------ idempotency
def test_the_same_artefacts_produce_one_job(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    first = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    db.commit()
    second = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    db.commit()

    assert first.id == second.id
    assert db.query(PublishJob).count() == 1


def test_republishing_already_published_artefacts_is_refused(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    job.youtube_video_id = "ALREADYPUBLISHED"
    job.status = RunStatus.SUCCESS.value
    db.commit()

    with pytest.raises(Conflict) as exc:
        publishing_service.create_publish_job(
            db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
        )
    assert "already published" in exc.value.message


def test_the_idempotency_key_changes_with_the_render(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    import uuid as uuid_module

    first = publishing_service.idempotency_key(publishable, publishable.current_render_asset_id)
    second = publishing_service.idempotency_key(publishable, uuid_module.uuid4())
    assert first != second, "a new render must be publishable"


def test_executing_an_already_uploaded_job_does_not_upload_again(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    job.youtube_video_id = "ALREADYUP"
    db.commit()

    with respx.mock:
        route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200))
        publishing_service.execute_publish(db, job)
        assert route.call_count == 0, "a job with a video id must never upload again"


# ---------------------------------------------------------------------- upload
@respx.mock
def test_a_successful_publish_uploads_verifies_and_records(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))
    respx.put(SESSION_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO)
    )
    verify = respx.get(VIDEOS_URL).mock(
        return_value=httpx.Response(200, json={"items": [feeds.YOUTUBE_UPLOADED_VIDEO]})
    )

    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    publishing_service.execute_publish(db, job)
    db.commit()

    assert job.status == RunStatus.SUCCESS.value
    assert job.youtube_video_id == "FIXTUREUPLOAD1"
    assert job.verified_at is not None, "the upload must be verified by reading it back"
    assert verify.call_count == 1

    video = db.query(YouTubeVideo).one()
    assert video.youtube_video_id == "FIXTUREUPLOAD1"
    assert video.privacy_status == "private"
    assert video.duration_seconds == 492
    assert publishable.status == "published"

    entry = db.query(AuditLog).filter(AuditLog.action == "publish.completed").one()
    assert "FIXTUREUPLOAD1" in (entry.summary or "")


@respx.mock
def test_a_failed_verification_does_not_mark_success(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    """An upload that cannot be read back is not a confirmed publish."""
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))
    respx.put(SESSION_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO))
    respx.get(VIDEOS_URL).mock(return_value=httpx.Response(200, json={"items": []}))

    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    with pytest.raises(ProviderUnavailable):
        publishing_service.execute_publish(db, job)
    db.commit()

    assert job.status != RunStatus.SUCCESS.value
    assert job.verified_at is None
    assert db.query(YouTubeVideo).count() == 0


@respx.mock
def test_a_transient_failure_schedules_a_bounded_retry(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(503, json={"error": {"message": "busy"}}))

    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    with pytest.raises(ProviderUnavailable):
        publishing_service.execute_publish(db, job)
    db.commit()

    assert job.status == RunStatus.QUEUED.value
    assert job.attempt_count == 1
    assert job.permanent_failure is False
    assert job.next_attempt_at is not None and job.next_attempt_at > datetime.now(UTC)


@respx.mock
def test_a_permanent_failure_is_not_retried(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(403, json=feeds.YOUTUBE_QUOTA_ERROR)
    )
    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    with pytest.raises(UpstreamPermanentError):
        publishing_service.execute_publish(db, job)
    db.commit()

    assert job.status == RunStatus.FAILED.value
    assert job.permanent_failure is True
    assert job.next_attempt_at is None


@respx.mock
def test_retries_stop_at_the_maximum(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(503, json={"error": {"message": "busy"}}))
    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    db.commit()

    for _ in range(publishing_service.MAX_UPLOAD_ATTEMPTS):
        with pytest.raises(ProviderUnavailable):
            publishing_service.execute_publish(db, job)
        db.commit()

    assert job.attempt_count == publishing_service.MAX_UPLOAD_ATTEMPTS
    assert job.status == RunStatus.FAILED.value
    assert job.permanent_failure is True


@respx.mock
def test_a_thumbnail_failure_does_not_lose_the_upload(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    from nexora.db.models import Thumbnail

    thumbnail_asset = asset_service.store_bytes(
        db,
        channel,
        kind="thumbnail",
        data=feeds.TINY_PNG,
        content_project_id=publishable.id,
        license_type="system_generated",
    )
    thumbnail = Thumbnail(
        content_project_id=publishable.id,
        asset_id=thumbnail_asset.id,
        generator="composite",
        status="approved",
        created_at=datetime.now(UTC),
    )
    db.add(thumbnail)
    db.flush()
    publishable.current_thumbnail_id = thumbnail.id
    db.commit()

    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))
    respx.put(SESSION_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO))
    # YouTube requires a verified channel for custom thumbnails; this can legitimately fail.
    respx.post(THUMBNAIL_URL).mock(
        return_value=httpx.Response(403, json={"error": {"message": "channel not verified"}})
    )
    respx.get(VIDEOS_URL).mock(
        return_value=httpx.Response(200, json={"items": [feeds.YOUTUBE_UPLOADED_VIDEO]})
    )

    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    publishing_service.execute_publish(db, job)
    db.commit()

    assert job.status == RunStatus.SUCCESS.value
    entry = db.query(AuditLog).filter(AuditLog.action == "publish.completed").one()
    assert "could not be applied" in (entry.summary or "")


@respx.mock
def test_metadata_reaches_youtube_intact(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    start = respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"location": SESSION_URL})
    )
    respx.put(SESSION_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO))
    respx.get(VIDEOS_URL).mock(
        return_value=httpx.Response(200, json={"items": [feeds.YOUTUBE_UPLOADED_VIDEO]})
    )

    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    publishing_service.execute_publish(db, job)

    body = json.loads(start.calls[0].request.content)
    assert body["snippet"]["title"] == "Why two bodies counted the same quarter differently"
    assert body["snippet"]["categoryId"] == "28"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["privacyStatus"] == "private"


def test_approval_is_recorded_and_audited(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    publishing_service.approve_project(db, publishable, approved=False, user_id=user.id, reason="Thin")
    db.commit()
    assert publishable.approval_status == "rejected"
    assert publishable.rejection_reason == "Thin"

    publishing_service.approve_project(db, publishable, approved=True, user_id=user.id)
    db.commit()
    assert publishable.approval_status == "approved"
    assert publishable.approved_at is not None
    assert db.query(AuditLog).filter(AuditLog.action == "project.approved").count() == 1


def test_the_publish_payload_never_leaks_tokens(
    db: Session, channel: Channel, publishable: ContentProject, user: User
) -> None:
    job = publishing_service.create_publish_job(
        db, channel, publishable, authorized_by=PublishAuthorization.USER, user_id=user.id
    )
    payload = str(publishing_service.job_to_dict(job))
    assert "fixture-access-token" not in payload
    assert "fixture-refresh-token" not in payload


def test_unverified_originality_warns_rather_than_blocking_forever(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    """No stored sources means originality is UNVERIFIED — reported, not silently passed.

    Blocking here would make a hand-written project unpublishable for ever, so it is a
    warning. The wording must never suggest it passed.
    """
    from nexora.db.models import ResearchDocument
    from nexora.services import quality as quality_service

    for document in db.query(ResearchDocument).all():
        document.text = None
    db.commit()

    check = quality_service.run_quality_check(db, channel, publishable)
    db.commit()

    originality = next(c for c in check.checks if c["key"] == "originality")
    assert originality["passed"] is False
    assert originality["blocking"] is False, "an unverifiable check must not block for ever"
    assert "UNVERIFIED" in originality["detail"]
    assert "not a pass" in originality["detail"]

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert result.can_publish, "an unverified check should warn, not block"


def test_conclusive_originality_below_threshold_does_block(
    db: Session, channel: Channel, publishable: ContentProject
) -> None:
    """A real plagiarism finding is a hard block, unlike an unverifiable one."""
    from nexora.db.models import ResearchDocument, ScriptVersion
    from nexora.services import quality as quality_service

    copied = "The regional foundry added forty thousand wafer starts in the quarter."
    document = db.query(ResearchDocument).first()
    document.text = copied
    version = db.get(ScriptVersion, publishable.current_script_version_id)
    version.narration_text = copied
    db.commit()

    check = quality_service.run_quality_check(db, channel, publishable)
    db.commit()

    originality = next(c for c in check.checks if c["key"] == "originality")
    assert originality["passed"] is False
    assert originality["blocking"] is True
    assert check.status == CheckStatus.FAIL.value

    result = publishing_service.run_preflight(
        db, channel, publishable, authorized_by=PublishAuthorization.USER
    )
    assert not result.can_publish
