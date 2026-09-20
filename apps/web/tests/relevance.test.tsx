import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  RelevanceBadge,
  RelevanceExplanation,
  RelevanceSummary,
} from "@/components/relevance";
import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";
import type { ChannelRelevance } from "@/lib/types";

function relevance(overrides: Partial<ChannelRelevance> = {}): ChannelRelevance {
  return {
    status: "RELEVANT",
    relevance_score: 62,
    score: 70,
    score_status: "SCORED",
    matched_categories: [{ category: "ai", tier: "primary", keywords: ["machine learning"] }],
    matched_preferences: [],
    excluded_by_rules: [],
    relevance_reasons: [
      { code: "category_match", detail: "Primary category 'ai' matched: machine learning" },
    ],
    available_source_count: 3,
    freshness: "FRESH",
    computed_at: "2026-09-20T10:00:00+00:00",
    score_breakdown: null,
    ...overrides,
  };
}

describe("RelevanceBadge", () => {
  it("names each status distinctly", () => {
    for (const [status, label] of [
      ["RELEVANT", "RELEVANT"],
      ["LOW_RELEVANCE", "LOW RELEVANCE"],
      ["EXCLUDED", "EXCLUDED"],
      ["INSUFFICIENT_DATA", "INSUFFICIENT DATA"],
    ] as const) {
      const { unmount } = render(<RelevanceBadge status={status} />);
      expect(screen.getByTestId("relevance-badge")).toHaveTextContent(label);
      unmount();
    }
  });

  it("does not render an unknown status as relevant", () => {
    const { container: unknown } = render(
      <RelevanceBadge status={"SOMETHING_NEW" as never} />,
    );
    const { container: relevant } = render(<RelevanceBadge status="RELEVANT" />);
    expect(unknown.firstElementChild?.className).not.toBe(
      relevant.firstElementChild?.className,
    );
  });
});

describe("RelevanceExplanation", () => {
  it("names the keyword that produced the match", () => {
    render(<RelevanceExplanation relevance={relevance()} channelName="Tech Desk" />);

    expect(screen.getByTestId("matched-ai")).toHaveTextContent("machine learning");
    expect(screen.getByText(/Relevance for Tech Desk/i)).toBeInTheDocument();
  });

  it("shows an exclusion as an instruction, naming the rule and the phrase", () => {
    render(
      <RelevanceExplanation
        relevance={relevance({
          status: "EXCLUDED",
          relevance_score: null,
          score: null,
          score_status: "EXCLUDED",
          matched_categories: [],
          excluded_by_rules: [
            {
              rule: "blocked_topics",
              value: "gambling",
              detail: "'gambling' appears in the item's title or summary.",
            },
          ],
          relevance_reasons: [
            { code: "excluded_by_channel_rule", detail: "Excluded by this channel's own rules: gambling." },
          ],
        })}
      />,
    );

    const rule = screen.getByTestId("exclusion-rule");
    expect(rule).toHaveTextContent("gambling");
    expect(rule).toHaveTextContent("blocked topics");
    expect(screen.getByRole("alert")).toHaveTextContent(/not ranked/i);
  });

  it("renders an unknown score as — with the reason, never as 0", () => {
    render(
      <RelevanceExplanation
        relevance={relevance({
          status: "INSUFFICIENT_DATA",
          relevance_score: null,
          score: null,
          score_status: "INSUFFICIENT_DATA",
          matched_categories: [],
          relevance_reasons: [
            {
              code: "no_matching_configuration",
              detail: "This channel has no categories and no preferred topics configured.",
            },
          ],
        })}
      />,
    );

    const figure = screen.getByTestId("figure-relevance");
    expect(figure).toHaveTextContent(UNKNOWN_PLACEHOLDER);
    expect(figure).not.toHaveTextContent("0");
    expect(figure.getAttribute("title")).toMatch(/Nothing configured/i);
  });

  it("states that no audience is modelled", () => {
    render(<RelevanceExplanation relevance={relevance()} />);
    expect(screen.getByText(/does not model your audience/i)).toBeInTheDocument();
    expect(screen.getByText(/rank differently for another channel/i)).toBeInTheDocument();
  });

  it("lists every stored reason", () => {
    render(
      <RelevanceExplanation
        relevance={relevance({
          relevance_reasons: [
            { code: "category_match", detail: "First reason." },
            { code: "preferred_topic_match", detail: "Second reason." },
          ],
        })}
      />,
    );
    expect(screen.getAllByTestId("relevance-reason")).toHaveLength(2);
  });

  it("shows a counted source total rather than an estimate", () => {
    render(<RelevanceExplanation relevance={relevance({ available_source_count: 4 })} />);
    expect(screen.getByText("4")).toBeInTheDocument();
  });
});

describe("RelevanceSummary", () => {
  it("names the categories that matched", () => {
    render(<RelevanceSummary relevance={relevance()} />);
    expect(screen.getByTestId("relevance-summary")).toHaveTextContent("ai");
  });

  it("says plainly when an item was excluded by the operator's own rule", () => {
    render(
      <RelevanceSummary
        relevance={relevance({
          status: "EXCLUDED",
          excluded_by_rules: [{ rule: "blocked_topics", value: "gambling", detail: "x" }],
        })}
      />,
    );
    expect(screen.getByTestId("relevance-summary")).toHaveTextContent(/Excluded.*gambling/);
  });

  it("reports insufficient configuration rather than implying a weak topic", () => {
    render(
      <RelevanceSummary
        relevance={relevance({
          status: "INSUFFICIENT_DATA",
          relevance_score: null,
          relevance_reasons: [
            { code: "no_matching_configuration", detail: "Configure the channel profile." },
          ],
        })}
      />,
    );
    const summary = screen.getByTestId("relevance-summary");
    expect(summary).toHaveTextContent("Configure the channel profile.");
    expect(summary.className).toMatch(/warn/);
  });

  it("explains a low-relevance item instead of leaving it unexplained", () => {
    render(
      <RelevanceSummary
        relevance={relevance({
          status: "LOW_RELEVANCE",
          relevance_score: 0,
          matched_categories: [],
          relevance_reasons: [
            { code: "no_category_match", detail: "No meaningful match with kids, anime." },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("relevance-summary")).toHaveTextContent(
      "No meaningful match with kids, anime.",
    );
  });
});
