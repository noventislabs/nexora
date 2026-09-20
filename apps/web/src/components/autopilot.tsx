import type { AutomationRun, AutomationState, GateResult, KillSwitchState } from "@/lib/types";
import { Card, StatusPill, UNKNOWN_PLACEHOLDER } from "@/components/primitives";

const STAGE_STYLES: Record<string, string> = {
  COMPLETED: "text-ok-500",
  SKIPPED: "text-base-500",
  STOPPED: "text-warn-500",
  FAILED: "text-danger-500",
};

/**
 * The global stop, shown wherever automation is.
 *
 * It renders as an alert rather than a quiet chip because while it is on, nothing in
 * the product produces or publishes anything, and a reader must not have to hunt for
 * that fact.
 */
export function KillSwitchBanner({ state }: { state: KillSwitchState }) {
  if (!state.engaged) return null;

  return (
    <div
      role="alert"
      data-testid="kill-switch-banner"
      className="rounded-lg border border-danger-500/40 bg-danger-500/10 px-4 py-3"
    >
      <p className="text-sm font-medium text-danger-500">
        Global emergency stop is engaged. No channel is producing or publishing
        anything.
      </p>
      {state.reason && (
        <p className="mt-1 text-xs text-base-300">Reason: {state.reason}</p>
      )}
      <p className="mt-2 text-[11px] text-base-500">{state.note}</p>
    </div>
  );
}

/**
 * Why automation can or cannot act, with each blocker named.
 *
 * Naming the scope matters: a global stop cannot be cleared by changing a channel
 * switch, and an operator who is not told that will try.
 */
export function GatePanel({
  title,
  gate,
}: {
  title: string;
  gate: GateResult;
}) {
  return (
    <div
      data-testid={`gate-${title.toLowerCase().replace(/\s+/g, "-")}`}
      className="rounded-lg border border-base-800 bg-base-850/60 px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-base-200">{title}</span>
        <StatusPill
          status={gate.allowed ? "AVAILABLE" : "UNAVAILABLE"}
          label={gate.allowed ? "ALLOWED" : "BLOCKED"}
        />
      </div>

      {gate.blockers.length > 0 && (
        <ul className="mt-2 space-y-1">
          {gate.blockers.map((blocker, index) => (
            <li key={index} data-testid="gate-blocker" className="text-xs text-base-400">
              <span className="font-mono text-[10px] uppercase text-base-500">
                {blocker.scope}
              </span>{" "}
              {blocker.detail}
            </li>
          ))}
        </ul>
      )}

      {gate.warnings.map((warning, index) => (
        <p key={index} className="mt-1 text-xs text-warn-500">
          {warning.detail}
        </p>
      ))}
    </div>
  );
}

/** What each autopilot level may do without a person. */
export function LevelTable({ state }: { state: AutomationState }) {
  const stages: [keyof AutomationState["level_capabilities"], string][] = [
    ["discover", "Discover"],
    ["research", "Research"],
    ["write", "Write"],
    ["produce", "Produce"],
    ["run_checks", "Run checks"],
    ["publish", "Publish"],
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.12em] text-base-500">
            <th className="py-2 pr-3 font-medium">Level</th>
            {stages.map(([key, label]) => (
              <th key={key} className="py-2 pr-3 font-medium">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(Object.keys(state.levels) as (keyof typeof state.levels)[]).map((level) => (
            <tr
              key={level}
              data-testid={`level-${level}`}
              className={`border-t border-base-800 ${
                level === state.mode ? "text-base-100" : "text-base-400"
              }`}
            >
              <td className="py-2 pr-3">
                {level.replace(/_/g, " ")}
                {level === state.mode && (
                  <span className="ml-2 text-[10px] text-accent-400">current</span>
                )}
              </td>
              {stages.map(([key]) => (
                <td key={key} className="py-2 pr-3">
                  <span
                    data-testid={`${level}-${key}`}
                    className={
                      state.levels[level][key] ? "text-ok-500" : "text-base-600"
                    }
                  >
                    {state.levels[level][key] ? "yes" : "no"}
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Today's activity, with the counting rule stated rather than assumed. */
export function DailyCounts({ state }: { state: AutomationState }) {
  return (
    <div>
      <dl className="grid grid-cols-3 gap-3">
        {[
          ["published_today", "Published", state.daily.published_today],
          ["scheduled_today", "Scheduled", state.daily.scheduled_today],
          ["failed_today", "Failed", state.daily.failed_today],
        ].map(([key, label, value]) => (
          <div key={key as string} className="rounded-lg border border-base-800 px-3 py-2">
            <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">
              {label as string}
            </dt>
            <dd
              data-testid={`daily-${key as string}`}
              className="mt-1 font-mono text-lg tabular-nums text-base-100"
            >
              {value as number}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-[11px] text-base-500">
        {state.daily.day} ({state.daily.timezone}) · limit{" "}
        {state.limits.max_videos_per_day}/day. {state.daily.counting_rule}
      </p>
    </div>
  );
}

/**
 * One run's stage-by-stage record.
 *
 * A stopped run is shown as a normal outcome, not an error: "no topic was relevant
 * enough today" is the system working correctly, and colouring it red would teach
 * operators to ignore real failures.
 */
export function RunTimeline({ run }: { run: AutomationRun }) {
  const seen = new Set(run.stages.map((stage) => stage.stage));

  return (
    <Card
      title={`Run ${run.id.slice(0, 8)} · ${run.trigger}`}
      action={
        <StatusPill
          status={
            run.status === "SUCCESS"
              ? "AVAILABLE"
              : run.status === "FAILED"
                ? "UNHEALTHY"
                : "NOT CONNECTED"
          }
          label={run.status}
        />
      }
    >
      {run.stopped_reason && (
        <p data-testid="run-stopped-reason" className="mb-3 text-sm text-warn-500">
          Stopped: {run.stopped_reason}
        </p>
      )}
      {run.error && (
        <p role="alert" className="mb-3 text-sm text-danger-500">
          {run.error}
        </p>
      )}

      <ol className="space-y-1.5">
        {run.pipeline.map((name) => {
          const stage = run.stages.find((entry) => entry.stage === name);
          return (
            <li
              key={name}
              data-testid={`stage-${name}`}
              className="flex items-start gap-3 border-b border-base-800 py-1.5 last:border-0"
            >
              <span
                className={`mt-0.5 w-20 shrink-0 font-mono text-[10px] ${
                  stage ? STAGE_STYLES[stage.status] : "text-base-700"
                }`}
              >
                {stage ? stage.status : "—"}
              </span>
              <div className="min-w-0">
                <p className="text-xs text-base-300">{name.replace(/_/g, " ")}</p>
                {stage && <p className="mt-0.5 text-xs text-base-500">{stage.detail}</p>}
              </div>
            </li>
          );
        })}
      </ol>

      {seen.size < run.pipeline.length && (
        <p className="mt-3 text-[11px] text-base-500">
          Stages marked {UNKNOWN_PLACEHOLDER} were never reached — the run ended before
          them.
        </p>
      )}
    </Card>
  );
}
