import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BaselineFigure, ComparisonPanel } from "@/components/analytics";
import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";
import type { BaselineResult, VideoComparison } from "@/lib/types";

const AVAILABLE: BaselineResult = {
  status: "AVAILABLE",
  metric: "views",
  median: 1200,
  sample_size: 9,
  observation_period: {
    start: "2026-06-22T00:00:00+00:00",
    end: "2026-09-20T00:00:00+00:00",
    days: 90,
  },
  range: { min: 300, max: 8400 },
  basis:
    "Median views across 9 videos published by this channel between 2026-06-22 and 2026-09-20. This channel only.",
};

const COMPARISON: VideoComparison = {
  metric: "views",
  value: 3600,
  observation:
    "This video recorded 3,600 views, above this channel's median of 1,200 across 9 videos published between 2026-06-22 and 2026-09-20.",
  compared_against: AVAILABLE,
  possible_factors: [
    { attribute: "published_weekday", value: 5, note: "Published on this weekday, in the channel's timezone." },
    { attribute: "duration_seconds", value: 480, note: "Runtime 8 minutes." },
  ],
  not_a_cause:
    "These are attributes this video shares with others, not reasons it performed as it did. NEXORA cannot isolate a cause.",
};

describe("BaselineFigure", () => {
  it("shows the sample size and observation period next to the median", () => {
    render(<BaselineFigure metric="views" baseline={AVAILABLE} />);

    const figure = screen.getByTestId("baseline-views");
    expect(figure).toHaveTextContent("1,200");
    expect(screen.getByTestId("sample-views")).toHaveTextContent("9");
    expect(figure).toHaveTextContent("90 days");
  });

  it("shows the real range rather than implying every video hit the median", () => {
    render(<BaselineFigure metric="views" baseline={AVAILABLE} />);
    expect(screen.getByTestId("baseline-views")).toHaveTextContent("300–8,400");
  });

  it("renders an absent baseline as — with the reason, never as 0", () => {
    render(
      <BaselineFigure
        metric="views"
        baseline={{
          status: "INSUFFICIENT_DATA",
          median: null,
          sample_size: 2,
          reason:
            "This channel has 2 comparable videos with a recorded views in the last 90 days. At least 5 are needed before a median means anything.",
        }}
      />,
    );

    const figure = screen.getByTestId("baseline-views");
    expect(figure).toHaveTextContent(UNKNOWN_PLACEHOLDER);
    expect(figure).toHaveTextContent(/At least 5 are needed/);
    expect(figure).not.toHaveTextContent(/median of 0/);
  });
});

describe("ComparisonPanel", () => {
  it("states the measurement with its baseline, sample and period", () => {
    render(<ComparisonPanel comparison={COMPARISON} />);

    expect(screen.getByTestId("observation")).toHaveTextContent("3,600 views");
    expect(screen.getByTestId("observation")).toHaveTextContent("median of 1,200");
    expect(screen.getByTestId("observation")).toHaveTextContent("across 9 videos");
    expect(screen.getByText(/This channel only/)).toBeInTheDocument();
  });

  it("labels factors as possible contributors, not causes", () => {
    render(<ComparisonPanel comparison={COMPARISON} />);

    expect(screen.getByText(/Possible contributing factors/i)).toBeInTheDocument();
    expect(screen.getByTestId("factor-duration_seconds")).toHaveTextContent("Runtime 8 minutes.");
    expect(screen.getByTestId("not-a-cause")).toHaveTextContent(/not reasons it performed/);
    expect(screen.getByTestId("not-a-cause")).toHaveTextContent(/cannot isolate a cause/);
  });

  it("never renders a forecast or a causal claim", () => {
    const { container } = render(<ComparisonPanel comparison={COMPARISON} />);
    const text = container.textContent?.toLowerCase() ?? "";

    for (const phrase of [
      "will get",
      "will go viral",
      "caused the",
      "guaranteed",
      "you will earn",
      "expect to",
      "predicted",
    ]) {
      expect(text).not.toContain(phrase);
    }
  });

  it("shows this video and the channel median side by side", () => {
    render(<ComparisonPanel comparison={COMPARISON} />);
    expect(screen.getByText("3,600")).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("omits the factors section when there is nothing to list", () => {
    render(<ComparisonPanel comparison={{ ...COMPARISON, possible_factors: [] }} />);
    expect(screen.queryByText(/Possible contributing factors/i)).not.toBeInTheDocument();
    // The disclaimer stays, because the absence of factors is not a causal claim either.
    expect(screen.getByTestId("not-a-cause")).toBeInTheDocument();
  });
});
