"""Request/response models shared across routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(ApiModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(ApiModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class UserResponse(ApiModel):
    id: str
    email: str
    display_name: str
    role: str
    onboarding_completed: bool


class SessionResponse(ApiModel):
    user: UserResponse
    csrf_token: str
    expires_at: str


class CreateChannelRequest(ApiModel):
    name: str = Field(default="NEXORA Global", max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    primary_language: str = Field(default="en", max_length=16)
    secondary_language: str | None = Field(default="bn", max_length=16)
    timezone: str = Field(default="Asia/Dhaka", max_length=64)
    categories: list[str] | None = None


class UpdateChannelRequest(ApiModel):
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    primary_language: str | None = Field(default=None, max_length=16)
    secondary_language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    categories: list[str] | None = None


class ChannelSettingsRequest(ApiModel):
    default_video_format: Literal["long_form", "short"] | None = None
    target_duration_min_seconds: int | None = Field(default=None, ge=15, le=7200)
    target_duration_max_seconds: int | None = Field(default=None, ge=15, le=7200)
    narration_tone: str | None = Field(default=None, max_length=64)
    call_to_action: str | None = Field(default=None, max_length=2000)
    aspect_ratio: Literal["16:9", "9:16"] | None = None
    resolution: Literal["720p", "1080p"] | None = None
    subtitle_burn_in: bool | None = None
    preferred_voice_id: str | None = Field(default=None, max_length=128)
    editorial_notes: str | None = Field(default=None, max_length=4000)
    #: Tri-state. ``None`` means undecided, which blocks publishing rather than
    #: defaulting to either answer — the declaration carries legal weight.
    made_for_kids_default: bool | None = None
    youtube_category_id: str | None = Field(default=None, max_length=16)


class AutomationSettingsRequest(ApiModel):
    mode: Literal["assisted", "semi_autonomous", "autonomous"] | None = None
    autopilot_enabled: bool | None = None
    auto_publish_enabled: bool | None = None
    require_human_approval: bool | None = None
    max_videos_per_day: int | None = Field(default=None, ge=0, le=24)
    max_videos_per_week: int | None = Field(default=None, ge=0, le=168)
    min_interval_minutes: int | None = Field(default=None, ge=0, le=10080)
    publish_window_start_hour: int | None = Field(default=None, ge=0, le=23)
    publish_window_end_hour: int | None = Field(default=None, ge=0, le=23)
    preferred_publish_hour: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = Field(default=None, max_length=64)
    min_quality_score: int | None = Field(default=None, ge=0, le=100)
    min_originality_score: int | None = Field(default=None, ge=0, le=100)
    max_copyright_risk: Literal["low", "medium", "high"] | None = None
    block_on_unknown_license: bool | None = None
    require_fact_check_pass: bool | None = None
    daily_scan_enabled: bool | None = None
    daily_scan_hour: int | None = Field(default=None, ge=0, le=23)


class EmergencyStopRequest(ApiModel):
    reason: str = Field(default="Operator emergency stop", max_length=1000)


class Page(ApiModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class ErrorResponse(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
