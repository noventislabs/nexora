"""Channel profiles: who a channel is for.

This is the configuration the relevance engine reads. Everything in it was typed by a
person — NEXORA never infers a channel's audience from its name, its category or the
videos it has published. A channel called "kiddo anime Tales" is not automatically a
children's channel as far as this code is concerned, because acting on that guess is
how a made-for-kids declaration ends up wrong.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import ValidationError
from nexora.db.models import Channel, ChannelProfile
from nexora.db.models.enums import AudienceClassification

MAX_LIST_ENTRIES = 50
MAX_ENTRY_CHARS = 120

#: Fields a person must have set for the profile to count as complete. Deliberately
#: short: these are the ones the relevance engine cannot work without.
REQUIRED_FOR_COMPLETE = ("audience_classification",)


def get_profile(session: Session, channel_id: uuid.UUID) -> ChannelProfile:
    """The channel's profile, creating the row on first read.

    Creating it here keeps channels made before this table existed working, and the
    row is all-defaults with ``audience_classification`` NULL, so it claims nothing.
    """
    profile = session.execute(
        select(ChannelProfile).where(ChannelProfile.channel_id == channel_id)
    ).scalar_one_or_none()
    if profile is None:
        profile = ChannelProfile(channel_id=channel_id)
        session.add(profile)
        session.flush()
    return profile


def validate_audience_classification(value: str | None) -> str | None:
    if value is None:
        return None
    allowed = {item.value for item in AudienceClassification}
    if value not in allowed:
        raise ValidationError(
            f"'{value}' is not a recognised audience classification.",
            details={"allowed": sorted(allowed)},
        )
    return value


def clean_phrase_list(values: Any, *, field: str) -> list[str]:
    """Normalize an operator-supplied list of phrases.

    These are matched literally against trend text, so they are lowercased and
    de-duplicated but otherwise kept exactly as typed — NEXORA does not stem them,
    expand them or infer synonyms, because the operator needs to be able to predict
    what their own rule will do.
    """
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValidationError(f"'{field}' must be a list of phrases.")
    cleaned: list[str] = []
    for item in values:
        phrase = str(item).strip().lower()
        if not phrase:
            continue
        if len(phrase) > MAX_ENTRY_CHARS:
            raise ValidationError(
                f"Each entry in '{field}' must be {MAX_ENTRY_CHARS} characters or fewer."
            )
        if phrase not in cleaned:
            cleaned.append(phrase)
    if len(cleaned) > MAX_LIST_ENTRIES:
        raise ValidationError(f"'{field}' accepts at most {MAX_LIST_ENTRIES} entries.")
    return cleaned


def clean_language_list(values: Any) -> list[str]:
    if values is None:
        return []
    cleaned: list[str] = []
    for item in values:
        code = str(item).strip().lower()
        if not code:
            continue
        if not (2 <= len(code) <= 16):
            raise ValidationError(f"'{item}' is not a language code.")
        if code not in cleaned:
            cleaned.append(code)
    return cleaned


def update_profile(
    session: Session, channel: Channel, updates: dict[str, Any]
) -> ChannelProfile:
    """Apply a partial profile update.

    ``audience_classification`` is the one field that may be set *to* NULL, so an
    operator who realises they classified a channel wrongly can return it to
    undeclared rather than being stuck with a wrong answer.
    """
    from nexora.services import categories as category_service

    profile = get_profile(session, channel.id)

    if "audience_classification" in updates:
        profile.audience_classification = validate_audience_classification(
            updates["audience_classification"]
        )

    if "secondary_categories" in updates and updates["secondary_categories"] is not None:
        known = category_service.known_keys(session, channel.id)
        primary = set(channel.categories or [])
        cleaned: list[str] = []
        for item in updates["secondary_categories"]:
            key = category_service.normalize_key(str(item))
            if key not in known:
                raise ValidationError(
                    f"Unknown category '{item}'.",
                    details={"known_categories": sorted(known)},
                )
            # A category cannot be both primary and secondary: the two carry different
            # weights, and silently keeping both would make the weighting unpredictable.
            if key in primary:
                raise ValidationError(
                    f"'{key}' is already a primary category for this channel. A category "
                    "is either primary or secondary, not both."
                )
            if key not in cleaned:
                cleaned.append(key)
        profile.secondary_categories = cleaned

    for field in ("preferred_topics", "blocked_topics", "content_exclusions",
                  "sensitive_content_restrictions"):
        if field in updates and updates[field] is not None:
            setattr(profile, field, clean_phrase_list(updates[field], field=field))

    if "secondary_languages" in updates and updates["secondary_languages"] is not None:
        profile.secondary_languages = clean_language_list(updates["secondary_languages"])

    for field in ("audience_description", "brand_voice", "country_region"):
        if field in updates:
            value = updates[field]
            setattr(profile, field, value.strip() if isinstance(value, str) and value.strip() else None)

    for field in ("translation_enabled", "short_form_enabled", "long_form_enabled"):
        if field in updates and updates[field] is not None:
            setattr(profile, field, bool(updates[field]))

    for field in ("preferred_duration_seconds", "target_videos_per_week"):
        if field in updates:
            value = updates[field]
            setattr(profile, field, int(value) if value is not None else None)

    if not profile.short_form_enabled and not profile.long_form_enabled:
        raise ValidationError(
            "A channel must allow at least one of short-form or long-form video, "
            "otherwise nothing can ever be produced for it."
        )

    overlap = set(profile.preferred_topics or []) & set(profile.blocked_topics or [])
    if overlap:
        raise ValidationError(
            "A topic cannot be both preferred and blocked: "
            + ", ".join(sorted(overlap))
            + ". Blocking always wins, so the preference would never take effect."
        )

    if all(getattr(profile, field) is not None for field in REQUIRED_FOR_COMPLETE):
        if profile.profile_completed_at is None:
            from datetime import UTC, datetime

            profile.profile_completed_at = datetime.now(UTC)

    session.flush()
    return profile


def languages_for(channel: Channel, profile: ChannelProfile) -> list[str]:
    """Every language this channel is willing to work from."""
    languages = [channel.primary_language]
    if channel.secondary_language:
        languages.append(channel.secondary_language)
    languages.extend(profile.secondary_languages or [])
    return [code for index, code in enumerate(languages) if code and code not in languages[:index]]


def matching_inputs(channel: Channel, profile: ChannelProfile) -> dict[str, Any]:
    """What the relevance engine actually has to work with, for reporting.

    The UI uses this to explain an ``INSUFFICIENT_DATA`` verdict by naming what is
    missing, rather than showing an unexplained blank.
    """
    return {
        "primary_categories": list(channel.categories or []),
        "secondary_categories": list(profile.secondary_categories or []),
        "preferred_topics": list(profile.preferred_topics or []),
        "blocked_topics": list(profile.blocked_topics or []),
        "content_exclusions": list(profile.content_exclusions or []),
        "sensitive_content_restrictions": list(profile.sensitive_content_restrictions or []),
        "languages": languages_for(channel, profile),
        "audience_classification": profile.audience_classification,
        "has_matchable_configuration": bool(
            (channel.categories or [])
            or (profile.secondary_categories or [])
            or (profile.preferred_topics or [])
        ),
    }


def profile_to_dict(channel: Channel, profile: ChannelProfile) -> dict[str, Any]:
    return {
        "channel_id": str(channel.id),
        "audience_description": profile.audience_description,
        "primary_categories": list(channel.categories or []),
        "secondary_categories": list(profile.secondary_categories or []),
        "audience_classification": profile.audience_classification,
        "country_region": profile.country_region,
        "primary_language": channel.primary_language,
        "secondary_languages": list(profile.secondary_languages or []),
        "translation_enabled": profile.translation_enabled,
        "short_form_enabled": profile.short_form_enabled,
        "long_form_enabled": profile.long_form_enabled,
        "preferred_duration_seconds": profile.preferred_duration_seconds,
        "target_videos_per_week": profile.target_videos_per_week,
        "brand_voice": profile.brand_voice,
        "preferred_topics": list(profile.preferred_topics or []),
        "blocked_topics": list(profile.blocked_topics or []),
        "content_exclusions": list(profile.content_exclusions or []),
        "sensitive_content_restrictions": list(profile.sensitive_content_restrictions or []),
        "profile_completed_at": (
            profile.profile_completed_at.isoformat() if profile.profile_completed_at else None
        ),
        "is_complete": profile.profile_completed_at is not None,
        "incomplete_fields": [
            field for field in REQUIRED_FOR_COMPLETE if getattr(profile, field) is None
        ],
        "matching_inputs": matching_inputs(channel, profile),
        "note": (
            "NEXORA matches trends against this configuration. It does not infer your "
            "audience, and it never derives the made-for-kids declaration from it."
        ),
    }
