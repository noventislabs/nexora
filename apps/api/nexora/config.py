"""Application configuration.

Every externally-provided credential is optional. When a credential is absent the
corresponding subsystem reports ``NOT_CONFIGURED`` instead of producing fabricated
data — see :mod:`nexora.services.availability`.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    app_env: str = "development"
    app_name: str = "NEXORA AI AUTOPILOT"
    app_secret: str = ""
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # --- Datastores ---------------------------------------------------------
    database_url: str = "postgresql+psycopg://nexora:nexora@localhost:5432/nexora"
    test_database_url: str = "postgresql+psycopg://nexora:nexora@localhost:5432/nexora_test"
    redis_url: str = "redis://localhost:6379/0"

    # --- Encryption ---------------------------------------------------------
    encryption_key: str = ""

    # --- Storage ------------------------------------------------------------
    storage_backend: str = "local"
    storage_local_path: str = "./data/storage"
    storage_endpoint: str = ""
    storage_region: str = "us-east-1"
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = ""

    # --- LLM ----------------------------------------------------------------
    llm_provider: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"

    # --- Voice --------------------------------------------------------------
    voice_provider: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"

    # --- YouTube ------------------------------------------------------------
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:8000/api/youtube/oauth/callback"
    youtube_api_key: str = ""

    # --- Trend sources ------------------------------------------------------
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "nexora-autopilot/0.1"

    # --- Media --------------------------------------------------------------
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    render_work_dir: str = "./data/render"

    # --- Worker -------------------------------------------------------------
    worker_concurrency: int = 1
    job_max_retries: int = 3

    # --- HTTP ---------------------------------------------------------------
    http_timeout_seconds: float = 30.0
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 240
    auth_rate_limit_per_minute: int = 10
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def cookie_secure(self) -> bool:
        return self.is_production or self.app_base_url.startswith("https://")


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
