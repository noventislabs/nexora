# NEXORA AI AUTOPILOT

Automated YouTube content research, production, publishing and analytics.

NEXORA reduces the operating workload of a YouTube channel by automating the pipeline:

```
TREND DISCOVERY → RESEARCH → TOPIC SELECTION → SCRIPT → FACT CHECK → VOICE → VIDEO
→ THUMBNAIL → METADATA → QUALITY CHECK → YOUTUBE UPLOAD → ANALYTICS → NEXT DECISION
```

## What this product does and does not claim

NEXORA optimises a workflow. It does **not** promise outcomes.

- YouTube revenue is **not** guaranteed.
- Monetization is **not** guaranteed.
- Views, subscribers and viral performance are **not** guaranteed.
- Copyright risk is handled explicitly; assets with an unestablished licence block
  autonomous publishing by default.
- YouTube's policies are respected. Authentication is OAuth 2.0 — NEXORA never asks
  for a YouTube password.

### No fabricated data, anywhere

This is enforced in code, not just in copy:

- Every integration reports a real `NOT CONFIGURED` / `UNAVAILABLE` state when its
  credentials are missing. Nothing falls back to invented output.
- A metric the system does not have is returned as `null` with an
  `unavailable_reason` and rendered as `—`. It is never rendered as `0`.
  (`apps/web/src/components/primitives.tsx` is the single place that decides this,
  and it is covered by tests.)
- Revenue is shown only when a supported monetization source actually returns it.
- FFmpeg reports `UNAVAILABLE` when the binary is absent; no placeholder video is
  ever produced.
- Health statuses come from real probes: PostgreSQL and Redis are round-tripped and
  FFmpeg is executed.
- A publish job is marked successful only after the video is **read back from
  YouTube**. Until that read confirms it, the UI says no video id has been confirmed —
  it never assumes an upload worked.
- Identifying a YouTube channel by its id is never presented as permission to upload
  to it. Upload, channel analytics, revenue and public read are four separate
  capabilities, shown separately as `GRANTED` / `NOT GRANTED`.
- NEXORA holds **no demographic data about your viewers and models none**. "Audience
  relevance" means text matched against categories and phrases you configured, and
  every match names the keyword that fired. A channel with nothing configured is
  reported as `INSUFFICIENT_DATA`, never as a low score.
- A channel's audience is never inferred from its name or its categories, and it is
  never derived to or from YouTube's made-for-kids declaration.
- Analytics are **measurements, not forecasts**. Every comparison states its
  observation period, its sample size and the baseline it was measured against, and
  below five comparable videos there is no baseline at all — just a stated reason.
  Attributes a video shares with others are labelled possible contributing factors;
  NEXORA does not claim to know what caused anything.

## Multi-channel

NEXORA is a multi-channel content operating system, not a product built around one
channel's subject matter. One account can run a kids channel, an anime channel, a
gaming channel and a finance channel side by side.

Each channel has its own profile — audience, categories, languages, brand voice,
preferred topics, blocked topics, content exclusions — and its own sources, pipeline,
YouTube connection and analytics. Nothing crosses between them: one channel's trends
never rank for another, and one channel's blocked topics never exclude anything for its
sibling.

Trend **ingestion is shared, ranking is per channel**. A feed added as a shared source
is fetched once for the account and stored once; each channel then reaches its own
verdict about every story in it. The same AI story comes back `RELEVANT` for the
technology channel and `LOW_RELEVANCE` for the kids channel, each with its own reasons
attached.

The content vocabulary is stored data, not a fixed list. 26 categories ship seeded —
kids, family, anime, animation, gaming, entertainment, music, education, science,
technology, AI, future, business, finance, digital economy, news, global developments,
history, documentary, commentary, lifestyle, health, sports, travel, food, DIY — and a
channel can add its own or redefine one for itself.

A blocked topic **excludes** an item rather than ranking it low: it is not scored, not
listed, and never offered as evidence when topics are generated. Ranking something you
forbade, even at the bottom, would be quietly ignoring the instruction.

## Architecture

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind CSS v4 |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 — the source of truth for all application state |
| Queue | Redis (signalling only) + a durable `jobs` table in PostgreSQL |
| Storage | S3-compatible abstraction (`local` filesystem or `s3`) |
| Media | FFmpeg |

```
nexora/
├── apps/
│   ├── api/          FastAPI service, worker, migrations, tests
│   └── web/          Next.js dashboard
├── docs/             Architecture, security and operations notes
├── docker-compose.yml
└── .env.example
```

The browser only ever talks to the Next.js origin. `/api/*` is proxied to FastAPI by a
server-side route handler (`apps/web/src/app/api/[...path]/route.ts`), which keeps the
session cookie same-origin and guarantees that no API key or OAuth secret can reach
client JavaScript.

## Quick start

### Docker (everything)

```bash
cp .env.example .env
# Generate the two required secrets:
python -c "import secrets; print('APP_SECRET=' + secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
docker compose up --build
```

Then open http://localhost:3000 — the first account you create becomes the owner.

### Local development

```bash
make setup      # install backend venv + frontend node_modules
make migrate    # apply database migrations
make api        # terminal 1 — API on :8000
make worker     # terminal 2 — background worker
make web        # terminal 3 — dashboard on :3000
```

Requires PostgreSQL 16, Redis 7 and FFmpeg on `PATH`.

## Configuration

Every credential is optional. Anything left blank turns the corresponding feature into
an explicit `NOT CONFIGURED` state rather than a broken button. See `.env.example` for
the full list; the essentials are:

| Variable | Purpose | Without it |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection | API will not start |
| `REDIS_URL` | Queue signalling | Queue reports `UNHEALTHY` |
| `APP_SECRET` | Session token fingerprints | Required in production |
| `ENCRYPTION_KEY` | Encrypts OAuth tokens at rest | YouTube connection blocked |
| `LLM_PROVIDER` + key | Topics, research, scripts, fact check | Those features report `NOT CONFIGURED` |
| `VOICE_PROVIDER` + key | Narration | `VOICE PROVIDER NOT CONFIGURED` |
| `FFMPEG_BINARY` | Video rendering | `FFMPEG UNAVAILABLE` — no placeholder video is written |
| `YOUTUBE_CLIENT_ID/SECRET` | Channel OAuth (upload, analytics) | Cannot connect a channel — uploading reports `NOT CONFIGURED` |
| `YOUTUBE_API_KEY` | Public channel and trend reads | Public statistics report `NOT CONFIGURED`; nothing is estimated |

Never commit `.env`; it is git-ignored, and `.env.example` contains placeholders only.

## Safety controls

| Control | Default |
|---|---|
| Autopilot | **OFF** |
| Auto-publishing | **OFF** |
| Human approval | **REQUIRED** |
| Automation mode | Assisted |
| Max videos / day | 1 |
| Block on unknown licence | ON |
| Require fact check to pass | ON |
| Upload privacy | **private** |
| Made-for-kids declaration | **NOT DECLARED** — blocks publishing until set |

Two contradictions are impossible by construction: auto-publishing cannot be enabled
while human approval is required, and autonomous mode cannot be selected without
turning the autopilot switch on in the same request. An **emergency stop** immediately
disables autopilot, disables auto-publishing and cancels queued publish jobs; clearing
it does *not* silently re-enable automation.

Publishing adds its own gates. `GET /api/publish/preflight/{project_id}` returns every
gate and its current state — emergency stop, rate limits, publishing window, render,
metadata, quality, copyright, YouTube connection and privacy — and `POST /api/publish`
returns **409 `safety_blocked`** naming each blocker rather than uploading. An operator
may override a blocked preflight; autopilot may not override it for itself.

`made_for_kids_default` is nullable on purpose: `NULL` means *undecided*, which is not
the same as "no". YouTube requires the declaration on every upload and it carries legal
weight, so an undecided channel cannot publish. Set it under
**Settings → YouTube upload defaults**.

### Connecting a YouTube channel

1. In Google Cloud Console, enable **YouTube Data API v3** and create an **OAuth 2.0
   Client ID** of type **Web application**.
2. Add `http://localhost:8000/api/youtube/oauth/callback` as an authorized redirect URI
   (use your real API origin in production).
3. Put the client id and secret in `.env` as `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`.
4. In the dashboard: **Channels → Manage YouTube → Connect YouTube**.

You sign in on Google's own page. NEXORA never sees, asks for, or stores a YouTube
password; it stores OAuth tokens encrypted with `ENCRYPTION_KEY` and nothing else.

## Testing

```bash
make test        # backend (pytest) + frontend (vitest)
make lint        # ruff + tsc --noEmit
```

The unit suite is hermetic: `conftest` blanks every third-party credential, so a local
`.env` can never change what it asserts and no test reaches a third party by accident.

Tests that *do* call a real API are marked `live` and excluded by default. Run them
with `pytest -m live`; each one skips itself when its credential is absent.

Backend tests run against real PostgreSQL and real Redis, and build the schema from the
actual Alembic migration. Only outbound third-party HTTP is intercepted, and every such
fixture is labelled as a test fixture.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — modules, data model, job queue, trend engine and the Opportunity Score formula
- [`docs/SECURITY.md`](docs/SECURITY.md) — credential handling, authentication, threat notes
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — running, migrating, backing up
