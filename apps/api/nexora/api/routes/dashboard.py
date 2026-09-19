"""Dashboard overview endpoint."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from nexora.api.deps import AuthUser, DbSession
from nexora.core.errors import NotFound
from nexora.services import channels as channel_service
from nexora.services import dashboard as dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None) -> dict[str, Any]:
    if channel_id is not None:
        channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    else:
        channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet. Create one to see the dashboard.")
    return dashboard_service.overview(db, channel)
