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

34 tables. Grouped by area:

- **Identity** — `users`, `auth_sessions`, `channels`, `channel_settings`,
  `automation_settings`, `youtube_connections`, `oauth_states`
- **Trends** — `trend_sources`, `trending_topics`, `topic_candidates`, `topic_research`,
  `research_documents`
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

## Content intelligence

### Research

A research run assembles its documents **first**, then shows the model exactly those
documents and nothing else.

Source material comes from the trend rows a candidate cites, plus their corroborating
rows. Where the channel has enabled `research_full_text_enabled`, the linked page is
also retrieved — but only after `robots.txt` has been consulted for our user agent, and
only up to a bounded excerpt (20k characters). Every outcome is stored on
`research_documents.fetch_decision`: `ALLOWED`, `BLOCKED_BY_ROBOTS`, `DISABLED`,
`NOT_ATTEMPTED` or `FAILED`. A refusal is recorded as a refusal.

Operator-supplied URLs are fetched server-side, so `validate_source_url` rejects
non-http schemes and any host that resolves to a private, loopback, link-local or
reserved address — including cloud metadata endpoints. Without that, a research source
would be an SSRF primitive.

What the model returns is then checked:

| Rule | Enforcement |
|---|---|
| Every fact, claim and statistic cites documents | Citations resolved by index; unresolvable ones are **dropped** |
| A citation only counts if the document carried text | `_resolve_indices` requires non-empty text |
| Classification is one of five values | Constrained to `FACT`/`CLAIM`/`ANALYSIS`/`OPINION`/`UNKNOWN` |
| An unsupported statement cannot be a FACT | Demoted to `UNKNOWN` regardless of the model's label |
| Conflicts are preserved | A conflict needs **two** independently cited positions, or it is dropped |

### Scripts

Every generation writes a new immutable `script_versions` row; nothing is overwritten,
so an earlier draft can always be reselected.

`estimated_duration_seconds` is derived from the word count at 150 wpm and is labelled
as an estimate everywhere it appears — it is not a measurement of the finished video.

**Originality** is checked by comparing normalized 9-word n-grams of the narration
against the stored source excerpts, reporting an overlap ratio, the longest verbatim
run and the matching spans. The result carries `conclusive: false` when no comparison
was possible (no source text, or narration shorter than the window), because a perfect
score with nothing to compare against is not evidence of originality. The `scope`
string states plainly that this is a check against what was actually read, not against
the whole web.

### Fact check

The model extracts assertions and proposes citations; the **verdict is computed here**:

* `SUPPORTED` — cites at least one document that exists and carried text.
* `UNSUPPORTED` — citations do not resolve, whatever the model claimed.
* `NEEDS_REVIEW` — the model flagged it as overstated, or it touches a subject the
  research recorded as contested.

Status is `FAIL` if anything is unsupported, `REVIEW` if anything needs review, `PASS`
otherwise. Extracting *no* checkable claims yields `REVIEW`, not `PASS` — unverified is
not the same as verified. A `FAIL` never advances the pipeline on its own.

## Media production

### Voice

Two adapters, and the difference between them is carried through the whole pipeline:

| Provider | Timings | Quota |
|---|---|---|
| `openai` | **None** — the audio endpoint returns audio only | Not exposed by the API; reported as such |
| `elevenlabs` | **Character-level**, via `with-timestamps` | Real, from `/user/subscription` |

Narration longer than a provider's per-request cap is split on sentence boundaries,
synthesized chunk by chunk, and concatenated with FFmpeg's concat demuxer. Where a
provider returns timings, each chunk's alignment is offset by the *measured* duration
of the chunks before it, so multi-chunk narration keeps real timings end to end.

Duration is always measured with `ffprobe` from the produced file. The script's
`estimated_duration_seconds` is a separate, clearly-labelled figure and is never
substituted for it.

### Subtitles

`timing_source` is either `provider` or `estimated`, and it propagates to the scene
plan, the render job, the WebVTT file (as a `NOTE` block) and the UI badge. Estimated
timing spreads the measured duration across cues in proportion to character count —
an approximation, labelled as one. If a provider's alignment cannot be walked cleanly,
the code falls back to estimation rather than emitting subtitles that drift.

### Scenes

One scene per script section, timed from the cues. Sections and cues describe the same
narration in the same order, so both are laid on one character axis and each cue goes
to the section containing its midpoint. The plan always covers exactly the audio that
exists — no gaps, no overrun.

### Rendering

FFmpeg composes a generated gradient background, per-scene typography and (optionally)
burned-in captions with the narration track, encoding H.264 + AAC at CRF 23.

Two security properties hold by construction:

* FFmpeg is spawned with `shell=False` and an argument vector. Shell metacharacters are
  therefore **inert**, and `validate_args` does not filter them — `;` is required
  `-filter_complex` syntax. What it *does* reject is FFmpeg's own protocol handling
  (`http://`, `file:`, `concat:`, `subfile:`, `pipe:` …) and control characters, since
  every path NEXORA passes is a local file in the render working directory.
* All on-screen text reaches FFmpeg through `textfile=` and `subtitles=` file
  references. No heading or caption is ever interpolated into the filter graph, so no
  caption can alter it.

Without FFmpeg the render is refused with `FFMPEG UNAVAILABLE`. No placeholder file is
ever written.

### Assets and licensing

Uploads are validated by **magic bytes**, not by filename or declared content type — a
PHP script named `photo.png` is rejected. `license_status` defaults to
`LICENSE UNKNOWN`, and anything other than `PERMITTED` sets
`blocks_autonomous_publishing`. A composite inherits the *least* permitted status of
its inputs, so a thumbnail built over an unknown-licence background is itself unknown.

Assets are content-addressed by SHA-256, so identical bytes are stored once. Downloads
are served `Content-Disposition: attachment` with `nosniff`, so an uploaded SVG or HTML
file can never execute on the application origin.

### Thumbnails

A real 1280×720 PNG composited with Pillow from channel branding and, optionally, an
operator-supplied background. Every candidate is kept and one must be approved before
it becomes the project's thumbnail. The output carries an explicit note that NEXORA
makes no claim about how it will perform — no CTR is predicted, estimated or implied.

## YouTube integration and publishing

### Two capabilities, kept apart

NEXORA treats "which channel is this" and "may I upload to it" as separate things,
because they are:

| | Needs | Grants | Cannot see |
|---|---|---|---|
| **Public read** | `YOUTUBE_API_KEY` | Title, subscriber count (when not hidden), view count, video count | Impressions, CTR, watch time, retention, revenue |
| **OAuth connection** | Channel owner's consent | Upload, channel analytics, (optionally) revenue | — |

`link_public_channel()` sets `public_channel_id` and deliberately never touches
`status`. A verified channel id is therefore visible in the UI as `public_read:
GRANTED, upload: NOT GRANTED`. Nothing in the codebase upgrades one to the other.

Metrics that only OAuth can supply are listed explicitly in
`METRICS_REQUIRING_OAUTH`, so a screen can say *why* a number is missing instead of
showing a blank or a zero. Public snapshots are stored with `is_complete=False`.

### OAuth 2.0

Authorization code flow with PKCE (S256), `access_type=offline` and
`prompt=consent`. NEXORA never sees a YouTube password — the credential is typed on
Google's own page.

- `state` is single-use, stored as an HMAC fingerprint, and expires after 10 minutes.
- The PKCE verifier is stored Fernet-sealed, since it is a credential for the exchange.
- `exchange_code` **raises** if Google returns no refresh token. Without one the
  connection would silently break an hour later, which is worse than failing now.
- Tokens are refreshed 5 minutes ahead of expiry. A failed refresh sets
  `status=revoked` rather than retrying forever, and the UI says so.
- `connection_to_dict()` returns capabilities and never token material.
- `/api/youtube/oauth/callback` is unauthenticated by design — Google calls it, not the
  browser session — and the single-use `state` is what binds the response to the
  request that started it.

### Upload

Resumable upload protocol, 8 MiB chunks. On a 308 the server's reported `Range` offset
wins over the local counter and the stream seeks to match, because Google's view of how
much it received is the authoritative one.

- One `PublishJob` per `(project, script version, render asset)`, keyed by a SHA-256
  `idempotency_key`. Re-publishing the same artefacts returns the existing job.
- `execute_publish()` returns early when `youtube_video_id` is already set, so a retried
  worker cannot create a duplicate video.
- Thumbnail upload is best-effort: its failure never loses an uploaded video.
- The video is **read back from YouTube** before the job is marked SUCCESS. Until that
  read confirms it, the UI says no video id has been confirmed.
- 3 attempts with 60s / 300s / 900s backoff. A permanent error (quota, permission,
  rejected content) is not retried.
- `publish_at` requires `privacy_status='private'`, which is YouTube's own rule.

### Preflight gates

`run_preflight()` evaluates every gate and returns all of them with their state, not
just the first failure:

`emergency_stop`, `autopilot_enabled`, `auto_publish_enabled`, `human_approval`,
`daily_limit`, `weekly_limit`, `minimum_interval`, `publishing_window`, `render`,
`metadata`, `quality_check`, `copyright_check`, `youtube_connection`, `privacy_status`.

The autopilot-only gates (`autopilot_enabled`, `auto_publish_enabled`,
`human_approval`) are omitted when a person publishes, because an operator pressing
Publish *is* the human approval.

An operator may override a blocked preflight with `force`. Autopilot may not:

```python
if force and authorized_by is PublishAuthorization.AUTOPILOT:
    raise SafetyBlocked("Autopilot may not override a blocked preflight.")
```

### Quality and copyright checks

Deterministic rules evaluated against the produced files. A model is never asked to
score the work, because a score a model invents is exactly the fabrication this
product refuses.

Two gates deserve naming:

- **Made-for-kids declaration.** `made_for_kids_default` is nullable on purpose:
  `NULL` means *undecided*, which is distinct from "no". YouTube requires the
  declaration on every upload and it carries legal weight, so an undecided channel
  blocks publishing. NEXORA does not guess it.
- **Originality.** A result below threshold *when there was something to compare
  against* is a real failure and blocks. A result with nothing to compare against is
  reported as `UNVERIFIED — This is not a pass`: a warning, not a block, because
  otherwise a hand-written project could never publish. The wording never claims it
  passed.

### Metadata

Title ≤ 100 characters, description ≤ 5000, tags ≤ 500 characters total and ≤ 30 each,
`<` and `>` rejected — YouTube's own limits, enforced before upload rather than
discovered during it. The source/attribution section is assembled **in code** from the
stored research documents, never written by the model, so attribution cannot be
hallucinated.

## Frontend

Next.js App Router. The dashboard is deliberately light for an 8 GB / i3 machine:

- No polling loops — screens expose an explicit Refresh control.
- Tables paginate server-side (25 rows).
- No chart or animation libraries; motion respects `prefers-reduced-motion`.
- `/api/*` is proxied at runtime by a route handler so the API host is a deploy-time
  setting rather than a build-time constant.

The `Metric` component is the only place that decides how an unknown value renders,
which is what makes a fabricated `0` impossible to introduce by accident.

Publishing screens follow the same rule. `GateRow` renders a failed gate as `BLOCKED`
or `WARNING` and never as `PASS`; `YouTubeConnectionCard` lists upload, analytics,
revenue and public-read as four separate `GRANTED / NOT GRANTED` rows, so a verified
channel id can never read as upload consent; and the OAuth return banner reports
`?youtube_error=` as a failure rather than rendering the optimistic case.
