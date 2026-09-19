# Architecture

## Principles

1. **PostgreSQL is the source of truth.** Redis carries queue wake-ups and nothing
   durable. Losing Redis delays work; it never loses it, because the worker also sweeps
   the `jobs` table for due work.
2. **Providers are pluggable and honest.** Every external integration implements a
   `Provider` subclass with an `availability()` method. If it cannot do the real thing,
   it says so — there is no third branch that returns invented data.
3. **State transitions are persisted and audited.** Anything an operator would need to
   explain later is written to `audit_logs`.

## Backend layout (`apps/api/nexora`)

| Module | Responsibility |
|---|---|
| `config.py` | Settings from environment; every credential optional |
| `core/` | Crypto (Fernet), passwords (Argon2id), typed errors, JSON logging with secret redaction |
| `db/models/` | SQLAlchemy models, one module per domain area |
| `migrations/` | Alembic revisions |
| `api/` | FastAPI routes, dependencies, middleware, schemas |
| `services/` | Domain logic: auth, channels, dashboard, health, availability, audit |
| `services/providers/` | Provider interfaces and implementations |
| `queue/` | Durable job queue, worker loop, handler registry |

## Provider interfaces

All in `nexora/services/providers/`:

| Interface | Purpose |
|---|---|
| `TrendProvider` | Fetch trend observations, normalized to `NormalizedTrend` |
| `LLMProvider` | Topic generation, research synthesis, scripts, fact-check reasoning |
| `VoiceProvider` | `generate_audio()`, `list_voices()`, `get_status()` |
| `StorageProvider` | S3-compatible object storage (`local`, `s3` implemented) |
| `YouTubeProvider` | OAuth, upload, thumbnail, video reads |
| `AnalyticsProvider` | Channel/video/revenue metrics, with explicit `unavailable` lists |

Each has a `ProviderRegistry` populated at import time, so adding a provider never
requires editing a call site.

`NormalizedTrend` keeps unsupplied fields as `None` rather than `0`, because
"zero views" and "views unknown" are different facts and scoring must distinguish them.

## Data model

33 tables. Grouped by area:

- **Identity** — `users`, `auth_sessions`, `channels`, `channel_settings`,
  `automation_settings`, `youtube_connections`, `oauth_states`
- **Trends** — `trend_sources`, `trending_topics`, `topic_candidates`, `topic_research`
- **Content** — `content_projects`, `content_scripts`, `script_versions`, `fact_checks`,
  `metadata_versions`, `quality_checks`, `copyright_checks`
- **Media** — `video_assets`, `voice_jobs`, `video_projects`, `video_render_jobs`, `thumbnails`
- **Publishing & analytics** — `publish_jobs`, `youtube_videos`, `analytics_snapshots`,
  `performance_observations`, `content_performance_features`
- **System** — `jobs`, `job_logs`, `automation_runs`, `audit_logs`, `system_settings`

Every datetime column is timezone-aware; the application works in UTC and converts to
the channel timezone only at the presentation edge.

`video_assets.license_status` defaults to `LICENSE UNKNOWN`, so an asset is
untrusted until proven otherwise rather than the reverse.

## Job queue

`jobs` rows are the durable record; Redis holds a wake-up list.

- Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, so multiple workers share a queue
  safely.
- Retries are bounded (`max_attempts`, default 3) with exponential backoff
  (30s → 120s → 600s, capped at 1h).
- A `ProviderNotConfigured` or `UpstreamPermanentError` failure is marked **permanent**
  and never retried — retrying cannot fix a missing API key.
- `idempotency_key` is unique; re-enqueueing returns the original job. This is what
  makes upload retries safe.
- Stalled jobs (no heartbeat) are reaped back to `QUEUED`.

Job types: `trend_scan`, `topic_generation`, `research`, `script_generation`,
`fact_check`, `voice_generation`, `asset_collection`, `video_render`,
`thumbnail_generation`, `metadata_generation`, `quality_check`, `youtube_upload`,
`analytics_sync`, `automation_tick`, `automation_advance`.

## Frontend

Next.js App Router. The dashboard is deliberately light for an 8 GB / i3 machine:

- No polling loops — screens expose an explicit Refresh control.
- Tables paginate server-side (25 rows).
- No chart or animation libraries; motion respects `prefers-reduced-motion`.
- `/api/*` is proxied at runtime by a route handler so the API host is a deploy-time
  setting rather than a build-time constant.

The `Metric` component is the only place that decides how an unknown value renders,
which is what makes a fabricated `0` impossible to introduce by accident.
