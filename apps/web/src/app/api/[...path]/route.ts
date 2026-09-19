import { NextRequest } from "next/server";

/**
 * Runtime reverse proxy to the FastAPI service.
 *
 * Why a route handler instead of a `next.config` rewrite: rewrites are resolved at
 * build time, so a container built once could not be pointed at a different API host
 * at deploy time. This handler reads `API_BASE_URL` per request instead.
 *
 * It also means the browser only ever talks to the Next.js origin, so the session
 * cookie stays same-origin and no API credential is ever present in client JavaScript.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_BASE_URL = () => process.env.API_BASE_URL ?? "http://localhost:8000";

/** Hop-by-hop and host-specific headers that must not be forwarded verbatim. */
const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authorization",
  "proxy-authenticate",
  "te",
  "trailer",
  "content-length",
]);

const STRIPPED_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
]);

async function proxy(request: NextRequest): Promise<Response> {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, API_BASE_URL());

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  // Let the API log the real client rather than the proxy hop.
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.arrayBuffer() : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      {
        code: "api_unreachable",
        message:
          "The NEXORA API is not reachable. Start the backend (docker compose up, or " +
          "uvicorn nexora.main:app) and try again.",
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (!STRIPPED_RESPONSE_HEADERS.has(lower) && lower !== "set-cookie") {
      responseHeaders.set(key, value);
    }
  });
  // Set-Cookie may appear several times; getSetCookie preserves each one.
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
