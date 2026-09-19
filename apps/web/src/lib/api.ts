/**
 * Typed client for the NEXORA API.
 *
 * Requests go to the Next.js origin and are proxied server-side, so the session
 * cookie is same-origin and no API credential is ever present in client JavaScript.
 */

export const CSRF_COOKIE = "nexora_csrf";
export const CSRF_HEADER = "x-nexora-csrf";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the failure is "you are not signed in" rather than a real fault. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** True when an integration is missing credentials rather than broken. */
  get isNotConfigured(): boolean {
    return this.code === "provider_not_configured";
  }
}

export function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  cache?: RequestCache;
};

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["content-type"] = "application/json";

  if (method !== "GET") {
    const csrf = readCsrfToken();
    if (csrf) headers[CSRF_HEADER] = csrf;
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    cache: options.cache ?? "no-store",
    signal: options.signal,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const record = (payload ?? {}) as Record<string, unknown>;
    throw new ApiError(
      response.status,
      typeof record.code === "string" ? record.code : "request_failed",
      typeof record.message === "string"
        ? record.message
        : `Request failed with status ${response.status}.`,
      record.details as Record<string, unknown> | undefined,
    );
  }
  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
