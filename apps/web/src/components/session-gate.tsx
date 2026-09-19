"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";
import { ErrorNotice, Loading } from "@/components/primitives";

/**
 * Client-side session check.
 *
 * The session cookie is HttpOnly, so the only way to know whether it is valid is to
 * ask the API. Unauthenticated visitors are sent to /login rather than shown an
 * empty dashboard.
 */
export function SessionGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<
    { status: "loading" } | { status: "ready"; user: CurrentUser } | { status: "error"; message: string }
  >({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    apiFetch<CurrentUser>("/api/auth/me")
      .then((user) => {
        if (!cancelled) setState({ status: "ready", user });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.isUnauthenticated) {
          router.replace("/login");
          return;
        }
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "Could not reach the NEXORA API.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (state.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loading label="Checking session" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="mx-auto max-w-lg px-4 py-20">
        <ErrorNotice message={state.message} />
        <p className="mt-3 text-sm text-base-400">
          The API may not be running. Start it with <code className="font-mono">docker compose up</code>{" "}
          or <code className="font-mono">uvicorn nexora.main:app</code>.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
