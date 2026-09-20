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

38 tables. Grouped by area:

- **Identity** — `users`, `auth_sessions`, `channels`, `channel_settings`,
  `channel_profiles`, `automation_settings`, `youtube_connections`, `oauth_states`
- **Automation** — `automation_runs`, `automation_locks`
- **Trends** — `trend_sources`, `trending_topics`, `content_categories`,
  `channel_topic_relevance`, `topic_candidates`, `topic_research`, `research_documents`
- **Content** — `content_projects`, `content_scripts`, `script_versions`, `fact_checks`,
  `metadata_versions`, `quality_checks`, `copyright_checks`
- **Media** — `video_assets`, `voice_jobs`, `video_projects`, `video_render_jobs`, `thumbnails`
- **Publishing & analytics** — `publish_jobs`, `youtube_videos`, `analytics_snapshots`,
  `performance_observations`, `content_performance_features`
- **System** — `jobs`, `job_logs`, `audit_logs`, `system_settings`

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

## Multi-channel architecture

NEXORA is not built around one channel. A user owns any number of channels, each with
its own profile, sources, pipeline, YouTube connection and analytics, and nothing
crosses between them.

```
User
 └── Channel
      ├── Channel profile        who it is for
      ├── Primary categories     on the channel row
      ├── Trend sources          its own, plus the account's shared ones
      ├── Relevance records      one per (channel, trend)
      ├── Content pipeline       research → script → media → publish
      ├── YouTube OAuth
      └── Analytics
```

Ingestion is global; ranking is per channel:

```
GLOBAL TREND INGESTION      shared sources fetched once for the account
        ↓
NORMALIZED TREND DATABASE   trending_topics, with a channel-independent signal_score
        ↓
CHANNEL RELEVANCE ENGINE    channel_topic_relevance, one row per (channel, trend)
        ↓
CHANNEL-SPECIFIC CANDIDATES → research → script → media → publish → analytics
```

Three channels reading the same feed fetch it once and store the story once. Each then
reaches its own verdict about it. A channel-scoped source still exists for a feed that
only makes sense for one channel; its rows carry that `channel_id` and no other channel
can see them.

### The content vocabulary

Categories live in `content_categories`, not in Python. The seed spans 26 categories —
kids, family, anime, animation, gaming, entertainment, music, education, science,
technology, AI, future, business, finance, digital economy, news, global developments,
history, documentary, commentary, lifestyle, health, sports, travel, food, DIY — and a
channel may add its own, or override a shared one for itself by defining a category
with the same key.

Each category carries the `keywords` the relevance engine matches on. That is the whole
mechanism, and it is why a match can always name the word that fired.

### Channel profile

`channel_profiles` holds what the relevance engine reads: audience description and
classification, secondary categories, region, secondary languages and whether
translation is enabled, short/long-form, brand voice, preferred topics, blocked topics,
content exclusions and sensitive-content restrictions.

`audience_classification` is nullable and is **never inferred**. A channel called
"Kiddo Anime Tales" sitting in the kids category still has it NULL until a person sets
it. It is also kept strictly separate from `made_for_kids_default`: one is NEXORA's
editorial notion of the audience, the other is a legal declaration YouTube requires,
and neither is ever derived from the other.

NEXORA holds no demographic data about viewers and models none. "Audience relevance"
here means text matching against stored configuration — nothing more is claimed.

### Channel relevance

One `channel_topic_relevance` row per (channel, trend), carrying the whole trace:
`matched_categories` (with the keywords that fired), `matched_preferences`,
`excluded_by_rules`, an ordered `relevance_reasons` list, `available_source_count` and
`freshness`.

Rules run in this order:

1. **Exclusions.** A blocked topic, content exclusion or sensitive-content restriction
   matching the text excludes the item outright — status `EXCLUDED`, no score at all.
   Ranking a forbidden topic at the bottom of the list would quietly ignore the
   operator's instruction, and an excluded row is never offered as evidence either.
2. **Language.** An item in a language the channel does not work in is excluded, unless
   translation is enabled, in which case it is kept and flagged.
3. **Category and preference matching**, at three weights: primary category 1.0,
   secondary 0.5, explicit preferred topic 1.5.

Phrases match on word boundaries, so blocking "war" does not block "warranty", and the
two-letter category keyword "ai" still matches the word "ai".

Four statuses, and `INSUFFICIENT_DATA` is first-class: a channel with no categories and
no preferred topics gives nothing to match against, and saying so is more honest than
returning a low number.

| Status | Meaning |
|---|---|
| `RELEVANT` | Matched above the threshold |
| `LOW_RELEVANCE` | Matched weakly or not at all |
| `EXCLUDED` | Matched a rule the operator configured |
| `INSUFFICIENT_DATA` | Nothing configured to match against, or no usable item text |

Relevance is **stored**, not computed on read, so editing a profile does not
retroactively rewrite history. `POST /api/channels/{id}/relevance/recompute` re-ranks
collected trends against the updated profile, and the UI says so rather than leaving
stale verdicts to be discovered.

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

It has two halves, because one normalized trend row serves several channels and a
single number folding in audience relevance could be true for at most one of them:

```
signal    = Σ(component_value × weight) / Σ(weight)      on the trend row
            over only the components whose inputs were present

opportunity = signal × 0.65 + channel_relevance × 0.35   on the relevance row
```

The **Signal Score** is a property of the item and identical for every channel:

| Component | Weight | Requires |
|---|---|---|
| Trend velocity | 0.28 | An engagement figure **and** a publication time |
| Competition | 0.19 | A scan of ≥5 items to compare against |
| Research material | 0.16 | A scan of ≥5 items |
| Recency | 0.15 | A publication time |
| Evergreen value | 0.12 | Item text |
| Source reliability | 0.10 | Always available |

Both halves are required. Each is blind to what the other measures — the signal cannot
say whether a channel should cover a story, and relevance cannot say whether the story
is worth covering — so an Opportunity Score built on one of them would be a different
quantity wearing this one's name. When either is unknown, the score is `null` with a
status naming which. A channel whose sources yield too little signal therefore sees
"insufficient data" rather than a reassuring number built from keyword overlap.

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
* `opportunity_score` is inherited from the cited item **this channel** ranked
  highest — its relevance row, not the trend's channel-independent signal. The model is
  never asked for a score, and a score it volunteers is ignored.
* Evidence is ordered by this channel's relevance, and an excluded row is never passed
  to the model at all.
* The prompt carries the channel's profile — audience, brand voice, preferred topics
  and a `must_not_cover` list — so the same evidence pool produces visibly different
  topics for a kids channel and a finance one.
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

## Analytics

Channel-scoped throughout. There is no cross-channel comparison endpoint, because a
median across channels with different audiences describes nothing that exists.

### Collection

Reading impressions, click-through rate, watch time, retention or revenue requires the
channel owner's OAuth consent with the analytics scope. Collection **refuses** without
it rather than falling back to public data: public reads cannot supply those metrics,
and a snapshot silently missing them would read as a channel that has none.

The Analytics API returns a column-header/row table. The adapter maps it back to named
metrics and reports what it asked for and did not get as `unavailable_metrics` — a
channel with 0 impressions and a channel whose impressions we may not read are
different facts, and the UI renders them differently.

Impressions and CTR are fetched as a **separate report**, because the API rejects an
entire request if one metric is invalid for it; losing the core metrics to chase
impressions would be a bad trade. Their failure costs those two metrics and marks them
unavailable. Revenue needs a further scope on top of analytics; without it the metric
is absent, never `0`, and it is never derived from views.

### Baselines

A baseline is one channel's own median for one metric. `Baseline` cannot be constructed
without a sample size and an observation period, because a median reported without them
invites exactly the over-reading this product refuses.

- Below **5** comparable videos there is no baseline. The result is
  `INSUFFICIENT_DATA` naming the count it has and the count it needs.
- A video published within the last **7 days** is excluded — it has not had time to
  behave like the others.
- A video with no recorded metric is excluded rather than counted as zero, which would
  drag every median down.
- A video is excluded from **its own** baseline; comparing it against a median it
  helped set would understate the difference.

### Observations

A comparison produces wording that is a measurement:

> This video recorded 3,600 views, above this channel's median of 1,200 across 9 videos
> published between 2026-06-22 and 2026-09-20.

Never "this topic performs well", and never a forecast. Attributes the video shares
with others — weekday, runtime, a question in the title — are stored as **possible
contributing factors**, with the payload stating that NEXORA cannot isolate a cause.
They are correlations within one channel's small sample, and the wording never says
otherwise.

`category_performance()` reports median views per topic category within one channel,
with a per-category sample size and an `INSUFFICIENT_DATA` status for any category
below the threshold.

## Autonomous automation

One traversal of the pipeline for one channel, from a collected trend to a verified
upload. The orchestrator holds a channel lock for the whole run, records each stage on
the `automation_runs` row, and resumes at the stage it died on rather than repeating an
LLM call and a render.

```
select_topic → research → script → fact_check → voice → render
             → thumbnail → metadata → quality_check → publish
```

A stage that cannot proceed sets a `stopped_reason` and the run ends cleanly as
STOPPED. "No topic was relevant enough today" is the system working, not an error. A
missing credential stops a run rather than failing it — a setup gap is a to-do, not a
crash — while a stage that genuinely raises is recorded in the stage list before the
run is marked FAILED.

### The stops, strictest first

| Scope | Halts |
|---|---|
| Global emergency stop (`system_settings`) | Every channel of every user |
| Channel emergency stop | One channel |
| `automation_enabled` | Producing content on one channel |
| `publishing_enabled` | Uploading from one channel, automated or manual |

Checked **server-side before every stage**, not once at the start: a run may take an
hour, and an operator who engages the stop during it expects the render in progress to
be the last thing that happens. Engaging the global stop also cancels queued upload and
automation jobs, since a queued job would otherwise run after the stop.

Releasing a stop re-enables nothing. Every channel keeps the settings it had, so
clearing a stop can never be a way to turn autopilot on.

### Levels

| Level | Discover | Research | Write | Produce | Run checks | Publish |
|---|---|---|---|---|---|---|
| Assisted | ✓ | ✓ | ✓ | ✓ | | |
| Semi-autonomous | ✓ | ✓ | ✓ | ✓ | ✓ | |
| Autonomous | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Autonomous publishing additionally requires `auto_publish_enabled`,
`publishing_enabled`, `require_human_approval` off, and the daily limit unspent.
Validation refuses configurations that contradict each other: autopilot cannot be on
while automation is off, auto-publish cannot be on while publishing is off or approval
is required, and an engaged stop blocks re-enabling any of them.

### Locks

`automation_locks` rows with a **partial unique index** on `(channel_id, lock_key)
WHERE released_at IS NULL`. Redis is the queue's wake-up signal and can lose a message;
losing a lock would let two workers research, script, render and upload the same topic,
and the second upload cannot be taken back.

An expired lock can be taken over so a worker that died mid-render does not strand its
channel, and the takeover is written onto the old row rather than overwriting it. A
released lock stays as the record that the run happened.

### Deduplication

Jaccard overlap on a fingerprint of significant tokens: deterministic, and recomputable
by hand from the stored `significant_tokens`. Never a similarity number a model
produced.

Checked against this channel's projects from the last 45 days, videos it published in
the last 90, and topics an operator already rejected. Scoped to one channel throughout
— two channels covering the same story is normal; the same channel doing it twice is
the problem. A topic with fewer than 3 distinctive tokens gets no fingerprint at all,
because a two-word fingerprint would match everything and silently drop real videos.

### The fact-check gate

Five per-claim verdicts, because the differences change what an operator should do:

| Verdict | Meaning | Blocks automated publishing |
|---|---|---|
| `SUPPORTED` | A research document that exists says this | |
| `PARTIALLY_SUPPORTED` | The research says something weaker | only if the channel requires a clean pass |
| `CONTRADICTED` | The research recorded disagreement the script resolves anyway | ✓ |
| `UNVERIFIED` | The research exists; none of it supports this | ✓ |
| `INSUFFICIENT_SOURCES` | Too little research to check anything | ✓ |

The rule: **an unverified claim is never turned into a factual statement by publishing
it.** A person may read a flagged claim and decide it is fine — they can check a source
automation cannot reach. Automation has no such option, so where a person may proceed
with a warning, automation stops.

One exception, off by default: a project explicitly marked as `commentary` on a channel
with `allow_unverified_commentary` may publish unverified premises as labelled opinion.
It never applies to the default `explainer` format, and never to a `CONTRADICTED` claim
— calling something opinion must not be a way around sources that actively disagree.

### Daily limits

Counted in the channel's own timezone, because "one per day" means one per the
operator's day. **Only a publish job verified on YouTube counts as published.** A
failed or unverified upload has published nothing and does not consume the day's
budget; failures are reported as a warning instead.

### Channel scoping

Every automation job carries `user_id` and `channel_id`, and
`gates.require_ownership` checks them against each other before the run starts. Without
it, a tampered `channel_id` would publish one user's content to another user's channel
using that channel's OAuth token.

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
