import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  CheckStatusBadge,
  CopyrightPanel,
  GateRow,
  PreflightPanel,
  QualityPanel,
} from "@/components/preflight";
import type { CopyrightCheck, Preflight, PreflightGate, QualityCheck } from "@/lib/types";

function gate(overrides: Partial<PreflightGate> = {}): PreflightGate {
  return {
    key: "render",
    label: "Rendered video",
    passed: true,
    blocking: true,
    detail: "A rendered file exists.",
    ...overrides,
  };
}

function preflight(overrides: Partial<Preflight> = {}): Preflight {
  return {
    can_publish: true,
    gates: [gate()],
    blockers: [],
    warnings: [],
    quality: null,
    copyright: null,
    ...overrides,
  };
}

describe("GateRow", () => {
  it("labels a passing gate PASS", () => {
    render(<GateRow gate={gate()} />);
    expect(screen.getByTestId("gate-render")).toHaveTextContent("PASS");
  });

  it("labels a failed blocking gate BLOCKED, never as a warning", () => {
    render(<GateRow gate={gate({ passed: false, blocking: true })} />);
    const row = screen.getByTestId("gate-render");
    expect(row).toHaveTextContent("BLOCKED");
    expect(row).not.toHaveTextContent("PASS");
  });

  it("labels a failed non-blocking gate WARNING rather than dressing it up as a pass", () => {
    render(<GateRow gate={gate({ passed: false, blocking: false })} />);
    const row = screen.getByTestId("gate-render");
    expect(row).toHaveTextContent("WARNING");
    expect(row).not.toHaveTextContent("PASS");
  });

  it("always shows the gate's own explanation", () => {
    render(<GateRow gate={gate({ passed: false, detail: "No render job has succeeded." })} />);
    expect(screen.getByText("No render job has succeeded.")).toBeInTheDocument();
  });
});

describe("PreflightPanel", () => {
  it("reports BLOCKED and names every blocker", () => {
    render(
      <PreflightPanel
        preflight={preflight({
          can_publish: false,
          blockers: ["Rendered video: No render exists.", "Metadata: Not generated."],
          gates: [gate({ passed: false })],
        })}
      />,
    );

    expect(screen.getByTestId("status-pill")).toHaveTextContent("BLOCKED");
    expect(screen.getByText("Rendered video: No render exists.")).toBeInTheDocument();
    expect(screen.getByText("Metadata: Not generated.")).toBeInTheDocument();
  });

  it("reports READY TO PUBLISH only when nothing blocks", () => {
    render(<PreflightPanel preflight={preflight()} />);
    expect(screen.getByTestId("status-pill")).toHaveTextContent("READY TO PUBLISH");
  });

  it("shows warnings separately and says they were not verified", () => {
    render(
      <PreflightPanel
        preflight={preflight({ warnings: ["Originality: UNVERIFIED — nothing to compare against."] })}
      />,
    );
    expect(screen.getByText(/not verified/i)).toBeInTheDocument();
    expect(
      screen.getByText("Originality: UNVERIFIED — nothing to compare against."),
    ).toBeInTheDocument();
  });

  it("never renders a blocked preflight as publishable", () => {
    render(<PreflightPanel preflight={preflight({ can_publish: false, blockers: ["x"] })} />);
    expect(screen.queryByText(/READY TO PUBLISH/)).not.toBeInTheDocument();
  });
});

describe("QualityPanel", () => {
  const quality: QualityCheck = {
    id: "q1",
    kind: "pre_publish",
    status: "FAIL",
    score: null,
    checks: [
      {
        key: "made_for_kids_declared",
        label: "Made-for-kids declaration",
        passed: false,
        blocking: true,
        detail: "NEXORA will not guess.",
      },
    ],
    details: {},
    created_at: null,
    blocks_publishing: true,
  };

  it("says the checks are deterministic, not model-scored", () => {
    render(<QualityPanel quality={quality} />);
    expect(screen.getByText(/No model was asked to score this work/i)).toBeInTheDocument();
  });

  it("renders a missing score as absent rather than inventing one", () => {
    render(<QualityPanel quality={quality} />);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByTestId("check-status")).toHaveTextContent("FAIL");
  });

  it("shows the undeclared made-for-kids gate as blocking", () => {
    render(<QualityPanel quality={quality} />);
    expect(screen.getByTestId("gate-made_for_kids_declared")).toHaveTextContent("BLOCKED");
  });
});

describe("CopyrightPanel", () => {
  const base: CopyrightCheck = {
    id: "c1",
    status: "PASS",
    risk_level: "low",
    unknown_license_count: 0,
    prohibited_count: 0,
    findings: [],
    created_at: null,
    blocks_publishing: false,
  };

  it("states plainly when nothing was found, rather than showing an empty panel", () => {
    render(<CopyrightPanel copyright={base} />);
    expect(screen.getByText(/No asset in this project/i)).toBeInTheDocument();
  });

  it("lists every finding with its explanation", () => {
    render(
      <CopyrightPanel
        copyright={{
          ...base,
          status: "FAIL",
          unknown_license_count: 2,
          findings: [{ label: "background.jpg", detail: "LICENSE UNKNOWN — origin not recorded." }],
        }}
      />,
    );
    expect(screen.getByTestId("check-status")).toHaveTextContent("FAIL");
    expect(screen.getByText("LICENSE UNKNOWN — origin not recorded.")).toBeInTheDocument();
  });
});

describe("CheckStatusBadge", () => {
  it("falls back to UNKNOWN styling for an unrecognised status rather than to PASS", () => {
    const { container: unknown } = render(<CheckStatusBadge status="SOMETHING_NEW" />);
    const { container: pass } = render(<CheckStatusBadge status="PASS" />);
    expect(unknown.firstElementChild?.className).not.toBe(pass.firstElementChild?.className);
  });
});
