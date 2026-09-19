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

## Trend engine

### Sources

Each `trend_sources` row is one provider instance — one RSS feed, one subreddit, one
YouTube chart/region. A source is validated by constructing its provider, so validation
can never drift from what the provider actually accepts.

Bounded caching is per source: `min_interval_minutes` (minimum 5) gates re-fetching, and
`next_allowed_at` enforces it. Consecutive failures add a backoff *on top of* the
interval (5 → 15 → 60 → 240 minutes, capped at 12 hours) so a broken source is not
hammered. A `NOT_CONFIGURED` source does not accrue failures — a missing API key is a
configuration gap, not an outage.

### Normalization

Every source reduces to `NormalizedTrend`: title, summary, url, category, language,
author, region, `published_at`, and a **sparse** engagement map. A metric the upstream
did not return is absent from the map — never zero. A YouTube channel that hides its
like count produces `{"views": N, "comments": M}` with no `likes` key at all.

### Deduplication

Two levels, both stored rather than discarded:

* `dedupe_hash` = `sha256(source_name | external_id)` — the same item re-fetched from
  the same source is skipped entirely.
* `content_hash` = `sha256(sorted significant title tokens)` — the same story told by a
  second source is stored and linked via `duplicate_of_id`, and the original's
  `corroboration_count` increases. Corroboration is evidence, so it is counted, not
  thrown away.

### Opportunity Score

Deliberately **not** a prediction of views, virality or revenue. It ranks how workable
a topic looks given the evidence actually collected. `GET /api/trends/scoring-model`
serves the live definition.

```
score = Σ(component_value × weight) / Σ(weight)
        over only the components whose inputs were present
```

| Component | Weight | Requires |
|---|---|---|
| Trend velocity | 0.22 | An engagement figure **and** a publication time |
| Audience relevance | 0.20 | Channel categories + item text |
| Competition | 0.15 | A scan of ≥5 items to compare against |
| Research material | 0.13 | A scan of ≥5 items |
| Recency | 0.12 | A publication time |
| Evergreen value | 0.10 | Item text |
| Source reliability | 0.08 | Always available |

A component with a missing input is **dropped**, not defaulted. If the computable
components carry less than 50% of the total weight, the score is reported as
`null` with an `unavailable_reason` rather than guessed. Every component records a
`basis` string explaining exactly what it measured, surfaced in the UI behind the score.

Competition measures overlap *within the configured sources only* — the basis string
says so, because the system cannot see all of YouTube. Per-source headline prefixes
("Ars Technica: …") are detected and stripped before overlap is measured, so shared
boilerplate never manufactures competition.

### Topic candidates

The LLM proposes an editorial angle; everything checkable is computed in code:

* Candidates cite trend rows by index. A candidate whose citations cannot be resolved
  is **dropped**, not stored with bare text.
* `opportunity_score` is inherited from the highest-scoring cited item. The model is
  never asked for a score, and a score it volunteers is ignored.
* Category is accepted only if the channel actually configured it.
* Freshness comes from the cited evidence timestamps, not from generation time.

With no LLM configured, `POST /api/topics/generate` returns HTTP 503
`provider_not_configured`. There is no offline idea generator.

## Frontend

Next.js App Router. The dashboard is deliberately light for an 8 GB / i3 machine:

- No polling loops — screens expose an explicit Refresh control.
- Tables paginate server-side (25 rows).
- No chart or animation libraries; motion respects `prefers-reduced-motion`.
- `/api/*` is proxied at runtime by a route handler so the API host is a deploy-time
  setting rather than a build-time constant.

The `Metric` component is the only place that decides how an unknown value renders,
which is what makes a fabricated `0` impossible to introduce by accident.
