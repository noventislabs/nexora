"use client";

import Link from "next/link";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  Metric,
  Stat,
  StatusPill,
  UNKNOWN_PLACEHOLDER,
  formatAge,
} from "@/components/primitives";
import type { DashboardOverview, SystemHealth } from "@/lib/types";
import { ApiError } from "@/lib/api";

export default function OverviewPage() {
  const overview = useApi<DashboardOverview>("/api/dashboard/overview");
  const health = useApi<SystemHealth>("/api/system/health");

  if (overview.status === "loading") return <Loading label="Loading overview" />;

  if (overview.status === "error") {
    const notFound = overview.error instanceof ApiError && overview.error.status === 404;
    if (notFound) {
      return (
        <EmptyState
          title="No channel yet"
          description="Create your channel to start the workflow. NEXORA ships with safe defaults: autopilot OFF, auto-publishing OFF and human approval required."
          action={
            <Link href="/dashboard/channels">
              <Button variant="primary">Create a channel</Button>
            </Link>
          }
        />
      );
    }
    return <ErrorNotice message={overview.error.message} />;
  }

  const { data } = overview;
  const { autopilot, today, channel_summary: summary } = data;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-base-100">Overview</h1>
          <p className="mt-0.5 text-sm text-base-400">
            {data.channel.name} · {data.channel.timezone}
          </p>
        </div>
        <Button onClick={() => { overview.refresh(); health.refresh(); }}>Refresh</Button>
      </header>

      <Card
        title="Autopilot"
        action={
          <Link href="/dashboard/automation" className="text-xs text-accent-400 hover:underline">
            Configure
          </Link>
        }
      >
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2.5">
            <span
              data-testid="autopilot-indicator"
              className={`h-2.5 w-2.5 rounded-full ${autopilot.enabled ? "bg-ok-500" : "bg-idle-500"}`}
              aria-hidden
            />
            <span className="font-mono text-base font-semibold tracking-wide text-base-100">
              {autopilot.enabled ? "ON" : "OFF"}
            </span>
          </div>
          <span className="text-xs uppercase tracking-[0.12em] text-base-500">
            {autopilot.mode.replace("_", " ")}
          </span>
          {autopilot.emergency_stop && <StatusPill status="UNHEALTHY" label="EMERGENCY STOP" />}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          <Stat
            label="Auto-publish"
            value={autopilot.auto_publish_enabled ? "ON" : "OFF"}
          />
          <Stat
            label="Human approval"
            value={autopilot.require_human_approval ? "REQUIRED" : "NOT REQUIRED"}
          />
          <Stat label="Max videos / day" value={autopilot.max_videos_per_day} />
        </div>

        {autopilot.emergency_stop && autopilot.emergency_stop_reason && (
          <p className="mt-3 text-xs text-danger-500">
            Stopped: {autopilot.emergency_stop_reason}
          </p>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title={`Today · ${today.timezone}`}>
          <Metric label="Trending topics" metric={today.trending_topics} />
          <Metric label="Ideas generated" metric={today.ideas_generated} />
          <Metric label="Scripts ready" metric={today.scripts_ready} />
          <Metric label="Videos rendering" metric={today.videos_rendering} />
          <Metric label="Scheduled" metric={today.scheduled} />
          <Metric label="Published" metric={today.published} />
          <p className="mt-3 text-[11px] text-base-500">
            Counts of work this system performed in the channel&rsquo;s local day.
          </p>
        </Card>

        <Card title="Channel" action={<StatusPill status={summary.youtube.status} />}>
          <p className="mb-2 text-sm text-base-100">{summary.name}</p>
          <Metric label="Subscribers" metric={summary.subscribers} />
          <Metric label="Views" metric={summary.views} />
          <Metric label="Videos" metric={summary.videos} />
          <Metric label="Revenue" metric={summary.revenue} />

          <div className="mt-3 space-y-1 text-[11px] text-base-500">
            <p>
              Source: {summary.data_source ?? UNKNOWN_PLACEHOLDER} · Last updated:{" "}
              {formatAge(summary.data_age_seconds)}
            </p>
            {!summary.revenue.available && summary.revenue.unavailable_reason && (
              <p className="text-base-400">{summary.revenue.unavailable_reason}</p>
            )}
            {summary.youtube.status !== "CONNECTED" && (
              <p>
                <Link href="/dashboard/channels" className="text-accent-400 hover:underline">
                  Connect YouTube
                </Link>{" "}
                to collect real channel data.
              </p>
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Needs your attention">
          <div className="grid grid-cols-2 gap-2.5">
            <Stat label="Pending approvals" value={data.pending_approvals} />
            <Stat label="Open ideas" value={data.open_ideas} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {(["QUEUED", "RUNNING", "FAILED", "SUCCESS"] as const).map((status) => (
              <Stat key={status} label={status} value={data.queue[status] ?? 0} />
            ))}
          </div>
          <p className="mt-3 text-[11px] text-base-500">Background job counts for this channel.</p>
        </Card>

        <Card
          title="System health"
          action={
            health.status === "ready" ? <StatusPill status={health.data.status} /> : undefined
          }
        >
          {health.status === "loading" && <Loading label="Probing components" />}
          {health.status === "error" && <ErrorNotice message={health.error.message} />}
          {health.status === "ready" && (
            <ul className="space-y-1.5">
              {health.data.components.map((component) => (
                <li key={component.name} className="flex items-center justify-between gap-3">
                  <span className="truncate font-mono text-xs uppercase tracking-wide text-base-300">
                    {component.name.replace(/_/g, " ")}
                  </span>
                  <span title={component.detail}>
                    <StatusPill status={component.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
