"use client";

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  StatusPill,
} from "@/components/primitives";
import {
  DailyCounts,
  GatePanel,
  KillSwitchBanner,
  LevelTable,
  RunTimeline,
} from "@/components/autopilot";
import type {
  AutomationRun,
  AutomationSettings,
  AutomationState,
  Channel,
  KillSwitchState,
  Paged,
} from "@/lib/types";

const MODES = [
  {
    value: "assisted",
    label: "Assisted",
    description:
      "AI researches, writes, voices and renders. You approve before anything is published. This is the default.",
  },
  {
    value: "semi_autonomous",
    label: "Semi-autonomous",
    description:
      "AI also selects topics and schedules uploads. Publishing still waits for approval unless you explicitly enable auto-publish.",
  },
  {
    value: "autonomous",
    label: "Autonomous",
    description:
      "The full pipeline runs unattended, within the limits below. Requires the autopilot switch to be ON.",
  },
] as const;

export default function AutomationPage() {
  const channels = useApi<Paged<Channel>>("/api/channels");
  const [channelId, setChannelId] = useState<string | null>(null);

  useEffect(() => {
    if (channels.status === "ready" && channelId === null && channels.data.items[0]) {
      setChannelId(channels.data.items[0].id);
    }
  }, [channels, channelId]);

  if (channels.status === "loading") return <Loading label="Loading channels" />;
  if (channels.status === "error") return <ErrorNotice message={channels.error.message} />;
  if (channels.data.items.length === 0) {
    return (
      <EmptyState
        title="No channel to automate"
        description="Create a channel first. Automation settings belong to a channel."
      />
    );
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Automation</h1>
        <p className="mt-0.5 text-sm text-base-400">
          The kill switches, autopilot level, publishing limits and safety thresholds.
          Every one of these is enforced on the server before any job runs.
        </p>
      </header>

      <GlobalKillSwitch />

      {channels.data.items.length > 1 && (
        <select
          value={channelId ?? ""}
          onChange={(event) => setChannelId(event.target.value)}
          className="rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100"
        >
          {channels.data.items.map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.name}
            </option>
          ))}
        </select>
      )}

      {channelId && <AutopilotPanel channelId={channelId} />}
      {channelId && <AutomationPanel channelId={channelId} />}
      {channelId && <RunHistory channelId={channelId} />}
    </div>
  );
}

function AutomationPanel({ channelId }: { channelId: string }) {
  const settings = useApi<AutomationSettings>(`/api/channels/${channelId}/automation`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function patch(body: Partial<AutomationSettings>) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/channels/${channelId}/automation`, { method: "PATCH", body });
      settings.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update automation settings.");
    } finally {
      setBusy(false);
    }
  }

  async function post(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/channels/${channelId}/automation/${path}`, { method: "POST", body });
      settings.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (settings.status === "loading") return <Loading label="Loading automation settings" />;
  if (settings.status === "error") return <ErrorNotice message={settings.error.message} />;

  const data = settings.data;

  return (
    <div className="space-y-5">
      {error && <ErrorNotice message={error} />}

      <Card title="Autopilot">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span
              data-testid="autopilot-state"
              className={`h-3 w-3 rounded-full ${data.autopilot_enabled ? "bg-ok-500" : "bg-idle-500"}`}
              aria-hidden
            />
            <div>
              <div className="font-mono text-lg font-semibold text-base-100">
                {data.autopilot_enabled ? "ON" : "OFF"}
              </div>
              <p className="text-xs text-base-400">
                When OFF, nothing is published automatically under any circumstance.
              </p>
            </div>
          </div>
          <Button
            variant={data.autopilot_enabled ? "default" : "primary"}
            disabled={busy || data.emergency_stop}
            onClick={() => patch({ autopilot_enabled: !data.autopilot_enabled })}
          >
            Turn {data.autopilot_enabled ? "OFF" : "ON"}
          </Button>
        </div>
        {data.emergency_stop && (
          <p className="mt-3 text-xs text-danger-500">
            Emergency stop is engaged — clear it before re-enabling autopilot.
          </p>
        )}
      </Card>

      <Card title="Emergency stop" action={data.emergency_stop ? <StatusPill status="UNHEALTHY" label="ENGAGED" /> : undefined}>
        <p className="text-sm text-base-300">
          Immediately prevents new publishing jobs and cancels queued uploads for this channel.
        </p>
        {data.emergency_stop ? (
          <div className="mt-4 space-y-3">
            <p className="text-xs text-base-400">
              Reason: {data.emergency_stop_reason ?? "—"}
              {data.emergency_stop_at ? ` · ${new Date(data.emergency_stop_at).toLocaleString()}` : ""}
            </p>
            <Button disabled={busy} onClick={() => post("clear-emergency-stop")}>
              Clear emergency stop
            </Button>
            <p className="text-[11px] text-base-500">
              Clearing the stop does not re-enable autopilot. You turn it back on deliberately.
            </p>
          </div>
        ) : (
          <Button
            variant="danger"
            className="mt-4"
            disabled={busy}
            onClick={() => post("emergency-stop", { reason: "Operator emergency stop" })}
          >
            STOP ALL PUBLISHING
          </Button>
        )}
      </Card>

      <Card title="Automation level">
        <div className="space-y-2.5">
          {MODES.map((mode) => (
            <label
              key={mode.value}
              className={`flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors ${
                data.mode === mode.value
                  ? "border-accent-600 bg-accent-600/10"
                  : "border-base-700 hover:border-base-600"
              }`}
            >
              <input
                type="radio"
                name="mode"
                className="mt-1"
                checked={data.mode === mode.value}
                disabled={busy}
                onChange={() =>
                  patch(
                    mode.value === "autonomous"
                      ? { mode: mode.value, autopilot_enabled: true }
                      : { mode: mode.value },
                  )
                }
              />
              <span>
                <span className="block text-sm font-medium text-base-100">{mode.label}</span>
                <span className="mt-0.5 block text-xs text-base-400">{mode.description}</span>
              </span>
            </label>
          ))}
        </div>
      </Card>

      <Card title="Publishing controls">
        <Toggle
          label="Require human approval"
          description="When ON, no video is published without an explicit approval."
          checked={data.require_human_approval}
          disabled={busy}
          onChange={(checked) => patch({ require_human_approval: checked })}
        />
        <Toggle
          label="Auto-publish"
          description="Can only be enabled while human approval is OFF. Both switches can never contradict each other."
          checked={data.auto_publish_enabled}
          disabled={busy || data.require_human_approval || data.emergency_stop}
          onChange={(checked) => patch({ auto_publish_enabled: checked })}
        />

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <NumberField
            label="Max videos / day"
            value={data.max_videos_per_day}
            min={0}
            max={24}
            disabled={busy}
            onCommit={(value) => patch({ max_videos_per_day: value })}
          />
          <NumberField
            label="Max videos / week"
            value={data.max_videos_per_week}
            min={0}
            max={168}
            disabled={busy}
            onCommit={(value) => patch({ max_videos_per_week: value })}
          />
          <NumberField
            label="Minimum interval (minutes)"
            value={data.min_interval_minutes}
            min={0}
            max={10080}
            disabled={busy}
            onCommit={(value) => patch({ min_interval_minutes: value })}
          />
          <NumberField
            label="Preferred publish hour"
            value={data.preferred_publish_hour}
            min={0}
            max={23}
            disabled={busy}
            onCommit={(value) => patch({ preferred_publish_hour: value })}
          />
          <NumberField
            label="Publishing window start hour"
            value={data.publish_window_start_hour}
            min={0}
            max={23}
            disabled={busy}
            onCommit={(value) => patch({ publish_window_start_hour: value })}
          />
          <NumberField
            label="Publishing window end hour"
            value={data.publish_window_end_hour}
            min={0}
            max={23}
            disabled={busy}
            onCommit={(value) => patch({ publish_window_end_hour: value })}
          />
        </div>
        <p className="mt-3 text-[11px] text-base-500">Times are in {data.timezone}.</p>
      </Card>

      <Card title="Quality and safety thresholds">
        <div className="grid gap-3 sm:grid-cols-2">
          <NumberField
            label="Minimum quality score"
            value={data.min_quality_score}
            min={0}
            max={100}
            disabled={busy}
            onCommit={(value) => patch({ min_quality_score: value })}
          />
          <NumberField
            label="Minimum originality score"
            value={data.min_originality_score}
            min={0}
            max={100}
            disabled={busy}
            onCommit={(value) => patch({ min_originality_score: value })}
          />
        </div>
        <div className="mt-3">
          <label className="block">
            <span className="mb-1.5 block text-xs text-base-400">Maximum copyright risk</span>
            <select
              value={data.max_copyright_risk}
              disabled={busy}
              onChange={(event) =>
                patch({ max_copyright_risk: event.target.value as AutomationSettings["max_copyright_risk"] })
              }
              className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 sm:w-56"
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
        </div>
        <div className="mt-3">
          <Toggle
            label="Block on unknown licence"
            description="Assets whose licence cannot be established block autonomous publishing."
            checked={data.block_on_unknown_license}
            disabled={busy}
            onChange={(checked) => patch({ block_on_unknown_license: checked })}
          />
          <Toggle
            label="Require fact check to pass"
            description="A FAIL result stops the pipeline before publishing."
            checked={data.require_fact_check_pass}
            disabled={busy}
            onChange={(checked) => patch({ require_fact_check_pass: checked })}
          />
        </div>
      </Card>
    </div>
  );
}

function Toggle({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 border-b border-base-800 py-3 last:border-0">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 accent-cyan-500"
      />
      <span>
        <span className="block text-sm text-base-100">{label}</span>
        <span className="mt-0.5 block text-xs text-base-400">{description}</span>
      </span>
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  return (
    <label className="block">
      <span className="mb-1.5 block text-xs text-base-400">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          const parsed = Number(draft);
          if (Number.isInteger(parsed) && parsed >= min && parsed <= max && parsed !== value) {
            onCommit(parsed);
          } else {
            setDraft(String(value));
          }
        }}
        className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 font-mono text-sm text-base-100 outline-none focus:border-accent-600"
      />
    </label>
  );
}


/**
 * The global stop.
 *
 * Engaging it needs a reason, because an operator reading the audit log next week
 * needs to know why the system halted, and "someone pressed the button" is not an
 * answer.
 */
function GlobalKillSwitch() {
  const state = useApi<KillSwitchState>("/api/automation/emergency-stop");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function engage(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<KillSwitchState>("/api/automation/emergency-stop", {
        method: "POST",
        body: { reason },
      });
      setNotice(
        `Stopped. ${result.cancelled_jobs ?? 0} queued job(s) were cancelled.`,
      );
      setReason("");
      state.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not engage the stop.");
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/automation/emergency-stop", { method: "DELETE" });
      setNotice(
        "Released. No channel's settings were changed — anything that was off is still off.",
      );
      state.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not release the stop.");
    } finally {
      setBusy(false);
    }
  }

  if (state.status !== "ready") return null;

  return (
    <div className="space-y-3">
      <KillSwitchBanner state={state.data} />
      {error && <ErrorNotice message={error} />}
      {notice && (
        <div className="rounded-lg border border-ok-500/30 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {notice}
        </div>
      )}

      <Card title="Global emergency stop">
        {state.data.engaged ? (
          <>
            <p className="text-sm text-base-300">
              Every channel of every user is halted. Releasing this turns nothing back
              on — each channel keeps the settings it had.
            </p>
            <div className="mt-3">
              <Button onClick={release} disabled={busy}>
                {busy ? "Releasing…" : "Release the stop"}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-base-400">
              Halts content production and publishing across every channel immediately,
              and cancels queued upload jobs.
            </p>
            <form onSubmit={engage} className="mt-3 flex flex-wrap items-end gap-2">
              <label className="block flex-1">
                <span className="mb-1.5 block text-xs text-base-400">
                  Why are you stopping? (recorded in the audit log)
                </span>
                <input
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  aria-label="Emergency stop reason"
                  placeholder="e.g. Upstream provider incident"
                  className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
                />
              </label>
              <Button type="submit" variant="danger" disabled={busy || reason.trim().length < 3}>
                Stop everything
              </Button>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}

/** What this channel's autopilot may do right now, and why. */
function AutopilotPanel({ channelId }: { channelId: string }) {
  const state = useApi<AutomationState>(`/api/automation/state?channel_id=${channelId}`, [
    channelId,
  ]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiFetch<AutomationRun>("/api/automation/run", {
        method: "POST",
        body: { channel_id: channelId, background: true },
      });
      setNotice(`Run ${result.id.slice(0, 8)} queued.`);
      state.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start a run.");
    } finally {
      setBusy(false);
    }
  }

  if (state.status === "loading") return <Loading label="Loading autopilot state" />;
  if (state.status === "error") return <ErrorNotice message={state.error.message} />;

  const data = state.data;

  return (
    <div className="space-y-4">
      {error && <ErrorNotice message={error} />}
      {notice && (
        <div className="rounded-lg border border-ok-500/30 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {notice}
        </div>
      )}

      <Card
        title="Autopilot"
        action={
          <Button
            variant="primary"
            onClick={run}
            disabled={busy || !data.can_produce.allowed}
          >
            {busy ? "Starting…" : "Run once now"}
          </Button>
        }
      >
        <div className="grid gap-3 lg:grid-cols-2">
          <GatePanel title="Produce content" gate={data.can_produce} />
          <GatePanel title="Publish without a person" gate={data.can_publish_autonomously} />
        </div>

        {data.active_locks[0] && (
          <p data-testid="active-lock" className="mt-3 text-xs text-base-400">
            A run is in progress ({data.active_locks[0].lock_key}), held by{" "}
            {data.active_locks[0].holder ?? "an unknown worker"}.
          </p>
        )}

        <p className="mt-3 text-[11px] text-base-500">{data.note}</p>
      </Card>

      <Card title="What each level may do unattended">
        <LevelTable state={data} />
      </Card>

      <Card title="Today">
        <DailyCounts state={data} />
      </Card>
    </div>
  );
}

function RunHistory({ channelId }: { channelId: string }) {
  const runs = useApi<{ items: AutomationRun[]; total: number }>(
    `/api/automation/runs?channel_id=${channelId}&limit=5`,
    [channelId],
  );

  if (runs.status !== "ready") return null;
  if (runs.data.total === 0) {
    return (
      <Card title="Runs">
        <p className="text-sm text-base-400">
          This channel has not run the pipeline yet.
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {runs.data.items.map((run) => (
        <RunTimeline key={run.id} run={run} />
      ))}
    </div>
  );
}
