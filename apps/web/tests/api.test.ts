import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, readCsrfToken, CSRF_HEADER } from "@/lib/api";

describe("apiFetch", () => {
  beforeEach(() => {
    document.cookie = "nexora_csrf=csrf-token-value; path=/";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "nexora_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("reads the CSRF token from the readable cookie", () => {
    expect(readCsrfToken()).toBe("csrf-token-value");
  });

  it("sends the CSRF header on state-changing requests", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    await apiFetch("/api/channels", { method: "POST", body: { name: "X" } });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>)[CSRF_HEADER]).toBe("csrf-token-value");
    expect(init.credentials).toBe("same-origin");
  });

  it("omits the CSRF header on reads", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));

    await apiFetch("/api/channels");

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>)[CSRF_HEADER]).toBeUndefined();
  });

  it("surfaces the backend error code and message", async () => {
    // A fresh Response per call: a Response body can only be consumed once.
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(
        JSON.stringify({ code: "provider_not_configured", message: "VOICE PROVIDER NOT CONFIGURED." }),
        { status: 503 },
      ),
    );

    await expect(apiFetch("/api/voice/generate", { method: "POST" })).rejects.toMatchObject({
      status: 503,
      code: "provider_not_configured",
    });

    try {
      await apiFetch("/api/voice/generate", { method: "POST" });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isNotConfigured).toBe(true);
    }
  });

  it("flags 401 as unauthenticated rather than a generic failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "authentication_required", message: "Sign in." }), {
        status: 401,
      }),
    );
    try {
      await apiFetch("/api/auth/me");
      expect.unreachable();
    } catch (error) {
      expect((error as ApiError).isUnauthenticated).toBe(true);
    }
  });

  it("handles 204 responses with no body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(apiFetch("/api/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
