import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  DailyCounts,
  GatePanel,
  KillSwitchBanner,
  LevelTable,
  RunTimeline,
} from "@/components/autopilot";
import type { AutomationRun, AutomationState, KillSwitchState } from "@/lib/types";

const LEVELS = {
  assisted: {
    discover: true, research: true, write: true, produce: true,
    run_checks: false, publish: false,
  },
  semi_autonomous: {
    discover: true, research: true, write: true, produce: true,
    run_checks: true, publish: false,
  },
  autonomous: {
    discover: true, research: true, write: true, produce: true,
    run_checks: true, publish: true,
  },
};

function state(overrides: Partial<AutomationState> = {}): AutomationState {
  return {
    channel_id: "c1",
    mode: "assisted",
    level_capabilities: LEVELS.assisted,
    switches: {
      automation_enabled: false,
      publishing_enabled: true,
      autopilot_enabled: false,
      auto_publish_enabled: false,
      require_human_approval: true,
      emergency_stop: false,
    },
    global_emergency_stop: {
      engaged: false, reason: null, engaged_at: null, engaged_by: null,
      scope: "global", note: "note",
    },
    can_produce: { allowed: false, blockers: [], warnings: [] },
    can_publish_autonomously: { allowed: false, blockers: [], warnings: [] },
    daily: {
      published_today: 0,
      scheduled_today: 0,
      failed_today: 0,
      day: "2026-09-20",
      timezone: "Asia/Dhaka",
      counting_rule:
        "Only a publish job verified on YouTube counts as published. A failed or retrying upload does not consume the day's limit.",
    },
    limits: { max_videos_per_day: 1, max_videos_per_week: 5, min_interval_minutes: 720 },
    active_locks: [],
    levels: LEVELS,
    note: "Every switch here is checked on the server before any job runs.",
    ...overrides,
  };
}

describe("KillSwitchBanner", () => {
  it("renders nothing while the stop is clear", () => {
    const { container } = render(
      <KillSwitchBanner
        state={{
          engaged: false, reason: null, engaged_at: null, engaged_by: null,
          scope: "global", note: "n",
        }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("is an alert naming the reason while engaged", () => {
    const engaged: KillSwitchState = {
      engaged: true,
      reason: "Upstream provider incident",
      engaged_at: "2026-09-20T10:00:00+00:00",
      engaged_by: "u1",
      scope: "global",
      note: "Checked on the server before every job runs.",
    };
    render(<KillSwitchBanner state={engaged} />);

    const banner = screen.getByTestId("kill-switch-banner");
    expect(banner).toHaveTextContent(/No channel is producing or publishing/i);
    expect(banner).toHaveTextContent("Upstream provider incident");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("GatePanel", () => {
  it("shows BLOCKED with each blocker and its scope", () => {
    render(
      <GatePanel
        title="Publish without a person"
        gate={{
          allowed: false,
          blockers: [
            { scope: "global", detail: "The global emergency stop is engaged." },
            { scope: "channel", detail: "Auto-publishing is OFF for this channel." },
          ],
          warnings: [],
        }}
      />,
    );

    expect(screen.getByTestId("status-pill")).toHaveTextContent("BLOCKED");
    const blockers = screen.getAllByTestId("gate-blocker");
    expect(blockers).toHaveLength(2);
    // The scope is named, so an operator does not try to fix a global stop with a
    // channel switch.
    expect(blockers[0]).toHaveTextContent("global");
    expect(blockers[1]).toHaveTextContent("channel");
  });

  it("shows ALLOWED with no blockers", () => {
    render(
      <GatePanel title="Produce content" gate={{ allowed: true, blockers: [], warnings: [] }} />,
    );
    expect(screen.getByTestId("status-pill")).toHaveTextContent("ALLOWED");
    expect(screen.queryByTestId("gate-blocker")).not.toBeInTheDocument();
  });

  it("shows a warning without marking the gate blocked", () => {
    render(
      <GatePanel
        title="Publish"
        gate={{
          allowed: true,
          blockers: [],
          warnings: [{ scope: "limit", detail: "Failures do not count toward the limit." }],
        }}
      />,
    );
    expect(screen.getByTestId("status-pill")).toHaveTextContent("ALLOWED");
    expect(screen.getByText(/do not count toward/)).toBeInTheDocument();
  });
});

describe("LevelTable", () => {
  it("shows that only autonomous may publish unattended", () => {
    render(<LevelTable state={state()} />);

    expect(screen.getByTestId("assisted-publish")).toHaveTextContent("no");
    expect(screen.getByTestId("semi_autonomous-publish")).toHaveTextContent("no");
    expect(screen.getByTestId("autonomous-publish")).toHaveTextContent("yes");
  });

  it("shows that assisted does not run checks unattended", () => {
    render(<LevelTable state={state()} />);
    expect(screen.getByTestId("assisted-run_checks")).toHaveTextContent("no");
    expect(screen.getByTestId("semi_autonomous-run_checks")).toHaveTextContent("yes");
  });

  it("marks the channel's current level", () => {
    render(<LevelTable state={state({ mode: "semi_autonomous" })} />);
    expect(screen.getByTestId("level-semi_autonomous")).toHaveTextContent("current");
    expect(screen.getByTestId("level-assisted")).not.toHaveTextContent("current");
  });
});

describe("DailyCounts", () => {
  it("separates published from failed and states the counting rule", () => {
    render(
      <DailyCounts
        state={state({
          daily: { ...state().daily, published_today: 0, failed_today: 3 },
        })}
      />,
    );

    expect(screen.getByTestId("daily-published_today")).toHaveTextContent("0");
    expect(screen.getByTestId("daily-failed_today")).toHaveTextContent("3");
    expect(
      screen.getByText(/Only a publish job verified on YouTube counts as published/),
    ).toBeInTheDocument();
  });

  it("shows the channel's own day and timezone", () => {
    render(<DailyCounts state={state()} />);
    expect(screen.getByText(/2026-09-20 \(Asia\/Dhaka\)/)).toBeInTheDocument();
  });
});

describe("RunTimeline", () => {
  const run: AutomationRun = {
    id: "11111111-2222-3333-4444-555555555555",
    channel_id: "c1",
    content_project_id: "p1",
    mode: "autonomous",
    trigger: "schedule",
    status: "SUCCESS",
    current_stage: null,
    stages: [
      { stage: "select_topic", status: "COMPLETED", detail: "Selected a topic.", data: {}, at: "t" },
      { stage: "research", status: "COMPLETED", detail: "Collected 4 documents.", data: {}, at: "t" },
      { stage: "script", status: "STOPPED", detail: "No LLM is configured.", data: {}, at: "t" },
    ],
    stopped_reason: "script: No LLM is configured.",
    error: null,
    started_at: "t",
    finished_at: "t",
    created_at: "t",
    pipeline: ["select_topic", "research", "script", "fact_check", "publish"],
  };

  it("shows every pipeline stage, including ones never reached", () => {
    render(<RunTimeline run={run} />);

    expect(screen.getByTestId("stage-select_topic")).toHaveTextContent("COMPLETED");
    expect(screen.getByTestId("stage-script")).toHaveTextContent("STOPPED");
    // Never reached: shown as unknown rather than silently omitted or marked passed.
    expect(screen.getByTestId("stage-publish")).toHaveTextContent("—");
    expect(screen.getByTestId("stage-fact_check")).toHaveTextContent("—");
  });

  it("reports a stopped run as a normal outcome, not an error", () => {
    render(<RunTimeline run={run} />);

    expect(screen.getByTestId("run-stopped-reason")).toHaveTextContent(
      "No LLM is configured.",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a genuine failure as an alert", () => {
    render(
      <RunTimeline
        run={{ ...run, status: "FAILED", stopped_reason: null, error: "Render crashed." }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Render crashed.");
  });

  it("never claims a video was published when the publish stage did not run", () => {
    const { container } = render(<RunTimeline run={run} />);
    expect(container.textContent).not.toMatch(/published/i);
  });
});
