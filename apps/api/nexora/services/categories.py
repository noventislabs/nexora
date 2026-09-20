"""The content-category vocabulary.

NEXORA is not a business-and-technology product that happens to allow other topics.
The vocabulary below is a **seed**, stored in the database so it can be extended,
edited and added to per channel. Nothing in the pipeline treats any key as special,
and a channel may invent categories the seed never anticipated.

Keywords are the whole mechanism: a category matches a trend because a term the
operator can read appears in the trend's title or summary. That is why every relevance
decision can be explained, and why none of it is a model's opinion about an audience.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, NotFound, ValidationError
from nexora.db.models import ContentCategory

_KEY_RE = re.compile(r"[^a-z0-9]+")

#: (key, label, audience_hint, sort_order, keywords)
#:
#: ``audience_hint`` is a hint for the UI only. It never sets a channel's audience
#: classification and never touches ``made_for_kids`` — both of those require a person.
SEED_CATEGORIES: list[tuple[str, str, str | None, int, tuple[str, ...]]] = [
    (
        "kids", "Kids", "kids", 10,
        ("kids", "children", "toddler", "preschool", "nursery rhyme", "cartoon", "kid-friendly",
         "for children", "playtime", "storytime", "bedtime story", "learning song", "abc",
         "counting", "colors for kids", "puppet"),
    ),
    (
        "family", "Family", "family", 20,
        ("family", "family-friendly", "parenting", "siblings", "all ages", "wholesome",
         "family movie", "family show", "parents", "household"),
    ),
    (
        "anime", "Anime", "general", 30,
        ("anime", "manga", "shonen", "shounen", "shoujo", "seinen", "isekai", "otaku",
         "studio ghibli", "crunchyroll", "cosplay", "light novel", "japanese animation",
         "season 2", "subbed", "dubbed"),
    ),
    (
        "animation", "Animation", "family", 40,
        ("animation", "animated", "cartoon", "cgi", "stop motion", "pixar", "dreamworks",
         "disney", "animator", "storyboard", "3d animation", "2d animation"),
    ),
    (
        "gaming", "Gaming", "teen", 50,
        ("game", "gaming", "gameplay", "console", "playstation", "xbox", "nintendo", "switch",
         "steam", "esports", "speedrun", "minecraft", "fortnite", "roblox", "rpg", "fps",
         "indie game", "patch notes", "dlc"),
    ),
    (
        "entertainment", "Entertainment", "general", 60,
        ("entertainment", "movie", "film", "series", "streaming", "netflix", "trailer",
         "box office", "celebrity", "award", "premiere", "season finale", "casting"),
    ),
    (
        "music", "Music", "general", 70,
        ("music", "song", "album", "artist", "band", "concert", "tour", "single", "chart",
         "producer", "remix", "soundtrack", "spotify"),
    ),
    (
        "education", "Education", "general", 80,
        ("education", "learning", "school", "university", "curriculum", "lesson", "teach",
         "student", "tutorial", "explainer", "course", "study", "exam", "literacy"),
    ),
    (
        "science", "Science", "general", 90,
        ("science", "research", "study", "physics", "biology", "chemistry", "climate",
         "space", "quantum", "experiment", "discovery", "peer-reviewed", "astronomy",
         "genetics", "neuroscience"),
    ),
    (
        "technology", "Technology", "general", 100,
        ("technology", "software", "hardware", "chip", "semiconductor", "cloud", "device",
         "platform", "engineering", "developer", "open source", "cyber", "security", "data",
         "smartphone", "laptop", "operating system"),
    ),
    (
        "ai", "AI", "general", 110,
        ("ai", "artificial intelligence", "machine learning", "neural", "model", "llm",
         "robotics", "automation", "agent", "training", "inference", "gpu", "chatbot",
         "generative", "diffusion", "transformer"),
    ),
    (
        "future", "Future", "general", 120,
        ("future", "2030", "next decade", "forecast", "emerging", "frontier", "prediction",
         "roadmap", "transition", "long term", "next generation"),
    ),
    (
        "business", "Business", "general", 130,
        ("business", "market", "economy", "startup", "company", "revenue", "funding",
         "investor", "acquisition", "ipo", "profit", "industry", "trade", "supply chain",
         "earnings", "merger", "layoffs"),
    ),
    (
        "finance", "Finance", "general", 140,
        ("finance", "stock", "bond", "interest rate", "inflation", "central bank", "currency",
         "investing", "portfolio", "dividend", "valuation", "crypto", "bitcoin", "etf"),
    ),
    (
        "digital_economy", "Digital economy", "general", 150,
        ("digital economy", "fintech", "payments", "e-commerce", "creator economy",
         "subscription", "marketplace", "digital currency", "remote work", "gig economy"),
    ),
    (
        "news", "News & current developments", "general", 160,
        ("breaking", "report", "announced", "election", "policy", "court", "investigation",
         "statement", "official", "authorities", "ruling", "legislation"),
    ),
    (
        "global_developments", "Global developments", "general", 170,
        ("global", "international", "policy", "regulation", "government", "treaty",
         "geopolitics", "worldwide", "nation", "summit", "sanctions", "diplomacy"),
    ),
    (
        "history", "History", "general", 180,
        ("history", "historical", "ancient", "empire", "war", "century", "archive",
         "civilisation", "civilization", "archaeology", "dynasty", "medieval"),
    ),
    (
        "documentary", "Documentary", "general", 190,
        ("documentary", "investigation", "long read", "deep dive", "untold", "inside story",
         "archival", "first-hand", "eyewitness"),
    ),
    (
        "commentary", "Commentary & analysis", "mature", 200,
        ("commentary", "analysis", "opinion", "critique", "essay", "perspective", "debate",
         "why ", "the case for", "the case against"),
    ),
    (
        "lifestyle", "Lifestyle", "general", 210,
        ("lifestyle", "routine", "habit", "productivity", "minimalism", "home", "design",
         "fashion", "style", "wellbeing", "self improvement"),
    ),
    (
        "health", "Health", "general", 220,
        ("health", "medical", "medicine", "nutrition", "fitness", "exercise", "mental health",
         "disease", "treatment", "clinical", "vaccine", "wellness", "diet"),
    ),
    (
        "sports", "Sports", "general", 230,
        ("sport", "football", "soccer", "cricket", "basketball", "tennis", "olympic",
         "tournament", "league", "match", "championship", "athlete", "transfer"),
    ),
    (
        "travel", "Travel", "general", 240,
        ("travel", "destination", "itinerary", "flight", "hotel", "tourism", "backpacking",
         "visa", "road trip", "city guide"),
    ),
    (
        "food", "Food & cooking", "family", 250,
        ("food", "recipe", "cooking", "cuisine", "chef", "baking", "restaurant", "ingredient",
         "meal", "kitchen", "street food"),
    ),
    (
        "diy", "DIY & how-to", "general", 260,
        ("diy", "how to", "build", "repair", "tutorial", "workshop", "craft", "handmade",
         "step by step", "maker"),
    ),
]


def normalize_key(value: str) -> str:
    """Fold a user-typed category name to a stable key."""
    key = _KEY_RE.sub("_", (value or "").strip().lower()).strip("_")
    if not key:
        raise ValidationError("A category key cannot be empty.")
    return key[:64]


def seed_builtin_categories(session: Session) -> int:
    """Insert any seed category the database does not already have.

    Idempotent, and it never overwrites an edited row: an operator who has tuned a
    category's keywords keeps their version.
    """
    existing = {
        row.key
        for row in session.execute(
            select(ContentCategory).where(ContentCategory.channel_id.is_(None))
        ).scalars()
    }
    inserted = 0
    for key, label, hint, order, keywords in SEED_CATEGORIES:
        if key in existing:
            continue
        session.add(
            ContentCategory(
                channel_id=None,
                key=key,
                label=label,
                keywords=list(keywords),
                audience_hint=hint,
                is_builtin=True,
                sort_order=order,
            )
        )
        inserted += 1
    if inserted:
        session.flush()
    return inserted


def list_categories(session: Session, channel_id: uuid.UUID | None = None) -> list[ContentCategory]:
    """The vocabulary visible to a channel: the shared set plus its own additions.

    A channel-owned category with the same key as a shared one wins, so a kids channel
    can narrow what "education" means for it without affecting anyone else.
    """
    condition = ContentCategory.channel_id.is_(None)
    if channel_id is not None:
        condition = condition | (ContentCategory.channel_id == channel_id)

    rows = list(
        session.execute(
            select(ContentCategory).where(condition).order_by(ContentCategory.sort_order)
        ).scalars()
    )
    by_key: dict[str, ContentCategory] = {}
    for row in rows:
        # Channel-owned rows are applied second so they replace the shared entry.
        if row.key not in by_key or row.channel_id is not None:
            by_key[row.key] = row
    return sorted(by_key.values(), key=lambda row: (row.sort_order, row.key))


def category_map(session: Session, channel_id: uuid.UUID | None = None) -> dict[str, list[str]]:
    """``{key: keywords}`` for the relevance engine."""
    return {
        row.key: [str(word).lower() for word in (row.keywords or [])]
        for row in list_categories(session, channel_id)
    }


def known_keys(session: Session, channel_id: uuid.UUID | None = None) -> set[str]:
    return {row.key for row in list_categories(session, channel_id)}


def create_category(
    session: Session,
    *,
    channel_id: uuid.UUID,
    key: str,
    label: str,
    keywords: list[str],
    description: str | None = None,
    audience_hint: str | None = None,
) -> ContentCategory:
    """Add a category this channel invented.

    Keywords are required: a category with none would match nothing, and silently
    accepting it would leave the operator wondering why their category never fires.
    """
    resolved = normalize_key(key)
    cleaned = [str(word).strip().lower() for word in keywords if str(word).strip()]
    if not cleaned:
        raise ValidationError(
            "A category needs at least one keyword. Keywords are what the relevance "
            "engine matches against — without them the category can never match."
        )

    duplicate = session.execute(
        select(ContentCategory).where(
            ContentCategory.channel_id == channel_id, ContentCategory.key == resolved
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise Conflict(f"This channel already defines the category '{resolved}'.")

    row = ContentCategory(
        channel_id=channel_id,
        key=resolved,
        label=label.strip() or resolved.replace("_", " ").title(),
        description=description,
        keywords=cleaned,
        audience_hint=audience_hint,
        is_builtin=False,
        sort_order=900,
    )
    session.add(row)
    session.flush()
    return row


def update_category(
    session: Session, channel_id: uuid.UUID, category_id: uuid.UUID, **changes: Any
) -> ContentCategory:
    """Edit a channel-owned category.

    A shared, built-in category cannot be edited in place — doing so would change the
    vocabulary for every other channel. Override it with a channel-owned category of
    the same key instead.
    """
    row = session.get(ContentCategory, category_id)
    if row is None:
        raise NotFound("Category not found.")
    if row.channel_id != channel_id:
        raise ValidationError(
            "This is a shared category. Create a channel category with the same key to "
            "override it for this channel; editing it here would change every channel."
        )
    for field, value in changes.items():
        if value is None:
            continue
        if field == "keywords":
            cleaned = [str(word).strip().lower() for word in value if str(word).strip()]
            if not cleaned:
                raise ValidationError("A category needs at least one keyword.")
            row.keywords = cleaned
        elif field in {"label", "description", "audience_hint"}:
            setattr(row, field, value)
    session.flush()
    return row


def delete_category(session: Session, channel_id: uuid.UUID, category_id: uuid.UUID) -> None:
    row = session.get(ContentCategory, category_id)
    if row is None:
        raise NotFound("Category not found.")
    if row.channel_id != channel_id:
        raise ValidationError("A shared category cannot be deleted.")
    session.delete(row)
    session.flush()


def category_to_dict(row: ContentCategory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "key": row.key,
        "label": row.label,
        "description": row.description,
        "keywords": list(row.keywords or []),
        "audience_hint": row.audience_hint,
        "is_builtin": row.is_builtin,
        "is_channel_owned": row.channel_id is not None,
        "sort_order": row.sort_order,
    }
