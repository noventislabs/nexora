"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ApiError | Error };

/**
 * Fetch-on-mount with manual refresh.
 *
 * Deliberately has no polling loop: repeatedly re-fetching unchanged data is exactly
 * the behaviour the performance budget rules out. Screens that need fresh data expose
 * a Refresh control instead.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): AsyncState<T> & {
  refresh: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    setState({ status: "loading" });
    apiFetch<T>(path, { signal: controller.signal })
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error : new Error("Request failed"),
        });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  return { ...state, refresh };
}
