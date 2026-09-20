"use client";

import { useState } from "react";
import Link from "next/link";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  ErrorNotice,
  Loading,
  NotConfigured,
  Stat,
  UNKNOWN_PLACEHOLDER,
} from "@/components/primitives";
import { BaselineFigure, ComparisonPanel } from "@/components/analytics";
import type {
  AnalyticsOverview,
  CategoryPerformance,
  VideoComparison,
  VideoWithMetrics,
} from "@/lib/types";

/** Metrics worth a tile. A key absent from a snapshot renders as — , never as 0. */
const SNAPSHOT_METRICS: [string, string][] = [
  ["views", "Views"],
  ["watch_time_minutes", "Watch minutes"],
  ["impressions", "Impressions"],
  ["click_through_rate", "CTR %"],
  ["average_view_percentage", "Avg view %"],
  ["subscribers_gained", "Subs gained"],
  ["likes", "Likes"],
  ["estimated_revenue", "Revenue"],
];

export default function AnalyticsPage() {
  const overview = useApi<AnalyticsOverview>("/api/analytics/overview");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (overview.status === "loading") return <Loading label="Loading analytics" />;
  if (overview.status === "error") return <ErrorNotice message={overview.error.message} />;

  const data = overview.data;
  const canRead = data.capabilities.channel_analytics === true;

  async function collect() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/analytics/collect", { method: "POST" });
      overview.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not collect analytics.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">
          Analytics — {data.channel_name}
        </h1>
        <p className="mt-0.5 text-sm text-base-400">{data.scope_note}</p>
      </header>

      {error && <ErrorNotice message={error} />}

      {data.oauth_app.status === "NOT CONFIGURED" ? (
        <NotConfigured
          feature="Channel analytics"
          detail={data.oauth_app.detail}
          missing={data.oauth_app.missing_settings}
        />
      ) : !canRead ? (
        <Card title="Analytics access">
          <p className="text-sm text-base-300">
            Reading impressions, click-through rate, watch time and retention requires
            this channel&apos;s owner to grant analytics access through Google. Public
            data cannot supply them, so NEXORA shows nothing here rather than numbers
            that would look like zeros.
          </p>
          <div className="mt-4">
            <Link href="/dashboard/channels">
              <Button variant="primary">Connect with analytics access</Button>
            </Link>
          </div>
        </Card>
      ) : (
        <Card
          title="Latest snapshot"
          action={
            <Button onClick={collect} disabled={busy}>
              {busy ? "Collecting…" : "Collect now"}
            </Button>
          }
        >
          {data.snapshot === null ? (
            <p className="text-sm text-base-400">
              No analytics have been collected for this channel yet.
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {SNAPSHOT_METRICS.map(([key, label]) => {
                  const value = data.snapshot!.metrics[key];
                  return (
                    <Stat
                      key={key}
                      label={label}
                      value={
                        value === undefined ? (
                          <span
                            data-testid={`unavailable-${key}`}
                            title={`Not reported by YouTube for this period. This is not zero.`}
                            className="text-base-500"
                          >
                            {UNKNOWN_PLACEHOLDER}
                          </span>
                        ) : (
                          value.toLocaleString()
                        )
                      }
                    />
                  );
                })}
              </div>
              <p className="mt-3 text-xs text-base-500">{data.snapshot.note}</p>
              {data.snapshot.unavailable_metrics.length > 0 && (
                <p className="mt-1 text-[11px] text-base-500">
                  Unavailable for this period:{" "}
                  <span className="font-mono">
                    {data.snapshot.unavailable_metrics.join(", ")}
                  </span>
                </p>
              )}
            </>
          )}
        </Card>
      )}

      <Card title="This channel's baselines">
        <p className="mb-3 text-xs text-base-400">
          A baseline is this channel&apos;s own median. It is never compared against
          another channel, and it needs a minimum number of videos before it means
          anything.
        </p>
        <div className="space-y-3">
          {Object.entries(data.baselines).map(([metric, baseline]) => (
            <BaselineFigure key={metric} metric={metric} baseline={baseline} />
          ))}
        </div>
      </Card>

      <CategoryPanel />
      <VideoPanel />

      <p className="text-xs text-base-500">{data.no_forecast_note}</p>
    </div>
  );
}

function CategoryPanel() {
  const data = useApi<CategoryPerformance>("/api/analytics/categories");
  if (data.status !== "ready") return null;

  return (
    <Card title="Median views by category">
      <p className="mb-3 text-xs text-base-400">
        Over {data.data.observation_period.days} days, within this channel only.
      </p>
      {data.data.categories.length === 0 ? (
        <p className="text-sm text-base-400">
          No published videos carry a topic category yet.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {data.data.categories.map((row) => (
            <li
              key={row.category}
              data-testid={`category-${row.category}`}
              className="flex items-baseline justify-between gap-4 border-b border-base-800 py-2 last:border-0"
            >
              <span className="text-sm text-base-200">{row.category}</span>
              <span className="text-right">
                {row.median_views === null ? (
                  <span className="font-mono text-sm text-base-500" title={row.reason}>
                    {UNKNOWN_PLACEHOLDER}
                  </span>
                ) : (
                  <span className="font-mono text-sm tabular-nums text-base-100">
                    {row.median_views.toLocaleString()}
                  </span>
                )}
                <span className="ml-2 text-[11px] text-base-500">
                  n={row.sample_size}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-base-500">{data.data.note}</p>
    </Card>
  );
}

function VideoPanel() {
  const videos = useApi<{ items: VideoWithMetrics[]; total: number }>("/api/analytics/videos");
  const [selected, setSelected] = useState<string | null>(null);

  if (videos.status !== "ready") return null;
  if (videos.data.total === 0) {
    return (
      <Card title="Videos">
        <p className="text-sm text-base-400">
          Nothing has been published from this channel yet, so there is nothing to
          measure.
        </p>
      </Card>
    );
  }

  return (
    <Card title="Videos">
      <ul className="space-y-1.5">
        {videos.data.items.map((video) => (
          <li key={video.video_id} className="border-b border-base-800 py-2 last:border-0">
            <button
              type="button"
              onClick={() =>
                setSelected((current) => (current === video.video_id ? null : video.video_id))
              }
              aria-expanded={selected === video.video_id}
              className="flex w-full items-baseline justify-between gap-4 text-left"
            >
              <span className="truncate text-sm text-base-200">{video.title}</span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-base-400">
                {video.metrics.views == null ? (
                  <span
                    data-testid="no-metrics"
                    title="No analytics have been collected for this video."
                    className="text-base-500"
                  >
                    {UNKNOWN_PLACEHOLDER}
                  </span>
                ) : (
                  `${video.metrics.views.toLocaleString()} views`
                )}
              </span>
            </button>
            {selected === video.video_id && <VideoComparisonPanel videoId={video.video_id} />}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function VideoComparisonPanel({ videoId }: { videoId: string }) {
  const data = useApi<{
    comparison: VideoComparison | null;
    unavailable?: { status: string; reason: string };
  }>(`/api/analytics/video/${videoId}`, [videoId]);

  if (data.status === "loading") return <Loading label="Comparing" />;
  if (data.status === "error") return <ErrorNotice message={data.error.message} />;

  if (data.data.comparison === null) {
    return (
      <p data-testid="comparison-unavailable" className="mt-2 text-xs text-warn-500">
        {data.data.unavailable?.reason ??
          "There is not enough data on this channel to compare this video against."}
      </p>
    );
  }

  return (
    <div className="mt-3">
      <ComparisonPanel comparison={data.data.comparison} />
    </div>
  );
}
