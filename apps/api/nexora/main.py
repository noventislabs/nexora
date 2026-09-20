"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nexora.api.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from nexora.api.routes import analytics as analytics_routes
from nexora.api.routes import audience as audience_routes
from nexora.api.routes import auth as auth_routes
from nexora.api.routes import automation as automation_routes
from nexora.api.routes import channels as channel_routes
from nexora.api.routes import content as content_routes
from nexora.api.routes import dashboard as dashboard_routes
from nexora.api.routes import media as media_routes
from nexora.api.routes import system as system_routes
from nexora.api.routes import trends as trend_routes
from nexora.api.routes import youtube as youtube_routes
from nexora.config import settings
from nexora.core.errors import NexoraError
from nexora.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "api.starting",
        extra={"env": settings.app_env, "version": app.version},
    )
    yield
    logger.info("api.stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="NEXORA AI AUTOPILOT API",
        version="0.1.0",
        description=(
            "Backend for the NEXORA AI AUTOPILOT YouTube content pipeline. "
            "Integrations that are not configured report NOT_CONFIGURED; no endpoint "
            "returns simulated data."
        ),
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "x-nexora-csrf", "x-request-id"],
        expose_headers=["x-request-id"],
        max_age=600,
    )

    @app.exception_handler(NexoraError)
    async def nexora_error_handler(request: Request, exc: NexoraError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("api.error", extra={"code": exc.code, "path": request.url.path})
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "The request body or query parameters are invalid.",
                "details": {"errors": _safe_errors(exc)},
            },
        )

    app.include_router(auth_routes.router)
    app.include_router(channel_routes.router)
    app.include_router(audience_routes.router)
    app.include_router(audience_routes.catalog_router)
    app.include_router(analytics_routes.router)
    app.include_router(automation_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(system_routes.router)
    app.include_router(system_routes.jobs_router)
    app.include_router(system_routes.logs_router)
    app.include_router(trend_routes.router)
    app.include_router(trend_routes.topics_router)
    app.include_router(content_routes.research_router)
    app.include_router(content_routes.projects_router)
    app.include_router(content_routes.scripts_router)
    app.include_router(content_routes.factcheck_router)
    app.include_router(media_routes.voice_router)
    app.include_router(media_routes.assets_router)
    app.include_router(media_routes.video_router)
    app.include_router(media_routes.thumbnails_router)
    app.include_router(youtube_routes.youtube_router)
    app.include_router(youtube_routes.metadata_router)
    app.include_router(youtube_routes.publish_router)

    @app.get("/api/health", tags=["system"], include_in_schema=False)
    def health_alias() -> dict[str, Any]:
        from nexora.services.health import system_health

        return system_health()

    return app


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Strip submitted values out of validation errors so passwords never echo back."""
    cleaned = []
    for error in exc.errors():
        cleaned.append(
            {
                "location": list(error.get("loc", [])),
                "message": error.get("msg", "invalid"),
                "type": error.get("type", "value_error"),
            }
        )
    return cleaned


app = create_app()
