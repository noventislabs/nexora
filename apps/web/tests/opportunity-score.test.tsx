import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CompetitionBadge, OpportunityScore } from "@/components/opportunity-score";
import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";
import type { ScoreBreakdown } from "@/lib/types";

const breakdown: ScoreBreakdown = {
  score: 78,
  available: true,
  unavailable_reason: null,
  competition_level: "medium",
  available_weight: 0.85,
  method: "Weighted mean over only the components whose inputs were present.",
  components: [
    {
      key: "trend_velocity",
      label: "Trend velocity",
      weight: 0.22,
      value: 84,
      basis: "12,000 views/hour over 9h, log-scaled against a 20,000/hour reference.",
      available: true,
      rating: "High",
    },
    {
      key: "competition",
      label: "Competition",
      weight: 0.15,
      value: null,
      basis: "This scan was too small (fewer than 5 items) to measure it.",
      available: false,
      rating: "UNKNOWN",
    },
  ],
};

describe("OpportunityScore", () => {
  it("renders a real score with its scale", () => {
    render(<OpportunityScore score={78} breakdown={breakdown} />);
    const pill = screen.getByTestId("opportunity-score");
    expect(pill).toHaveTextContent("78");
    expect(pill).toHaveTextContent("/100");
  });

  it("renders a dash and the reason when the score is unavailable", () => {
    render(
      <OpportunityScore
        score={null}
        breakdown={{
          ...breakdown,
          score: null,
          available: false,
          unavailable_reason: "Not enough signal to score this item.",
        }}
      />,
    );
    const pill = screen.getByTestId("opportunity-score");
    expect(pill).toHaveTextContent(UNKNOWN_PLACEHOLDER);
    expect(pill).toHaveTextContent("not scorable");
    expect(pill).toHaveAttribute("title", "Not enough signal to score this item.");
    expect(pill.textContent).not.toMatch(/\d/);
  });

  it("stays a dash even when no breakdown is supplied", () => {
    render(<OpportunityScore score={null} breakdown={null} />);
    expect(screen.getByTestId("opportunity-score")).toHaveTextContent(UNKNOWN_PLACEHOLDER);
  });

  it("reveals the full component breakdown on demand", async () => {
    const user = userEvent.setup();
    render(<OpportunityScore score={78} breakdown={breakdown} />);

    await user.click(screen.getByTestId("opportunity-score"));

    expect(screen.getByText("Trend velocity")).toBeInTheDocument();
    expect(screen.getByText(/12,000 views\/hour/)).toBeInTheDocument();
    expect(screen.getByText(/Computed over 85% of the total weight/)).toBeInTheDocument();
  });

  it("marks an unavailable component as unavailable rather than zero", async () => {
    const user = userEvent.setup();
    render(<OpportunityScore score={78} breakdown={breakdown} />);
    await user.click(screen.getByTestId("opportunity-score"));

    const competitionRow = screen.getByText("Competition").closest("li");
    expect(competitionRow).not.toBeNull();
    expect(competitionRow!).toHaveTextContent("unavailable");
    expect(competitionRow!.textContent).not.toMatch(/\b0\b/);
  });

  it("never claims to predict performance", async () => {
    const user = userEvent.setup();
    const { container } = render(<OpportunityScore score={78} breakdown={breakdown} />);
    await user.click(screen.getByTestId("opportunity-score"));

    const text = container.textContent!.toLowerCase();
    // The disclaimer must be present, and it legitimately uses the word "virality".
    expect(text).toContain("not a prediction of views, virality or revenue");
    // What must never appear is a phrase that asserts an outcome.
    for (const claim of [
      "will go viral",
      "guaranteed",
      "probability of",
      "expected views",
      "projected revenue",
    ]) {
      expect(text).not.toContain(claim);
    }
  });
});

describe("CompetitionBadge", () => {
  it("shows a known level", () => {
    render(<CompetitionBadge level="low" />);
    expect(screen.getByText(/competition: low/)).toBeInTheDocument();
  });

  it("shows a dash for an unknown level instead of guessing", () => {
    render(<CompetitionBadge level="unknown" />);
    expect(screen.getByText(`competition: ${UNKNOWN_PLACEHOLDER}`)).toBeInTheDocument();
  });
});
