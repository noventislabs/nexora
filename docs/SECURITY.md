# Security

## Credentials

- **Nothing reaches the browser.** API keys and OAuth client secrets exist only in the
  server environment. The `/api/system/capabilities` endpoint reports *status*, never
  values. The frontend calls the API through a server-side proxy, so no key is ever in
  a client bundle.
- **`.env` is git-ignored**; `.env.example` contains placeholders only.
- **Logs are scrubbed.** `SecretRedactingFilter` replaces any configured credential
  found in a log message or a structured field with `***REDACTED***`.

## Authentication

- Passwords: **Argon2id** (`time_cost=2`, `memory_cost=64 MiB`, `parallelism=2`),
  minimum 12 characters.
- Sessions: a random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie
  (`Secure` in production). Only an HMAC fingerprint is stored, so a database
  disclosure yields no usable session credential.
- Session lifetime: 14 days absolute, 7 days idle.
- **CSRF**: double-submit. A non-`HttpOnly` CSRF cookie must be echoed in the
  `x-nexora-csrf` header on every state-changing request, and is compared in constant
  time against the session's stored fingerprint.
- Open registration closes after the first account, so a self-hosted deployment cannot
  be claimed by a stranger who finds the port open.
- Login failures are indistinguishable between "unknown email" and "wrong password".

## OAuth tokens

YouTube access and refresh tokens are sealed with **Fernet** (AES-128-CBC +
HMAC-SHA256) using `ENCRYPTION_KEY` before they reach the database. Connecting a
channel is blocked entirely when no key is configured, rather than storing plaintext.
NEXORA never requests, transports or stores a YouTube password.

The authorization code flow uses **PKCE** (S256). The `state` parameter is single-use,
stored only as an HMAC fingerprint, and expires after 10 minutes; the PKCE verifier is
itself a credential for the exchange, so it is stored Fernet-sealed and deleted once
used. `/api/youtube/oauth/callback` is deliberately unauthenticated — Google's redirect
carries no session cookie — and the single-use `state` is what binds the response to
the request that started it. An unknown, expired or replayed `state` fails closed: the
browser is redirected back with `?youtube_error=` and nothing is connected.

`connection_to_dict()` is the only serializer for a connection, and it returns
capabilities, scopes and timestamps — never token material, encrypted or otherwise. A
test asserts that no endpoint leaks a token, and that the ciphertext itself never
appears in a response body.

Disconnecting clears both encrypted tokens. A refresh Google rejects sets
`status=revoked` rather than retrying indefinitely, and the UI says the authorization
was revoked instead of showing the channel as connected.

## Input handling

- Request bodies use Pydantic models with `extra="forbid"`, so unexpected fields are
  rejected rather than silently ignored.
- Validation errors never echo submitted values back (a rejected password is not
  reflected in the response).
- Storage keys reject absolute paths, traversal segments, backslashes and null bytes —
  and *reject* rather than silently rewrite, so a caller bug stays visible.
- FFmpeg is invoked as an argument vector (never a shell string), from
  application-controlled values only, and every argument is screened for shell
  metacharacters. User input never becomes an FFmpeg flag.

## Transport and headers

`x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy:
no-referrer` and `cross-origin-opener-policy: same-origin` are set by middleware on
both tiers. CORS allows only the configured origin and only the headers the app uses.

## Rate limiting

A fixed-window in-process limiter: 240 requests/minute per client for `/api/*`, and 10
per minute for `/api/auth/login` and `/api/auth/register`. A multi-instance deployment
should front this with a shared limiter.

## Authorization

Channel-scoped resources verify ownership on every access (`get_channel_for_user`
raises `PermissionDenied` for another user's channel). Accounts with the `viewer` role
cannot reach mutating endpoints.

## Reporting

Security issues should be reported privately to the repository owner before public
disclosure.
