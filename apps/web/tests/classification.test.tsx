import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ClassificationBadge,
  ClassificationLegend,
  FetchDecisionTag,
} from "@/components/classification";
import type { Classification } from "@/lib/types";

describe("ClassificationBadge", () => {
  it.each<Classification>(["FACT", "CLAIM", "ANALYSIS", "OPINION", "UNKNOWN"])(
    "renders %s with an explanation",
    (value) => {
      render(<ClassificationBadge value={value} />);
      const badge = screen.getByTestId(`classification-${value}`);
      expect(badge).toHaveTextContent(value);
      expect(badge.getAttribute("title")).toBeTruthy();
    },
  );

  it("distinguishes FACT from CLAIM visually, not just by text", () => {
    const { container: factBox } = render(<ClassificationBadge value="FACT" />);
    const { container: claimBox } = render(<ClassificationBadge value="CLAIM" />);
    expect(factBox.firstElementChild?.className).not.toBe(
      claimBox.firstElementChild?.className,
    );
  });

  it("falls back to UNKNOWN styling for an unexpected value", () => {
    render(<ClassificationBadge value={"MADE_UP" as Classification} />);
    expect(screen.getByTestId("classification-MADE_UP")).toBeInTheDocument();
  });

  it("explains that a CLAIM is not corroborated", () => {
    render(<ClassificationBadge value="CLAIM" />);
    expect(screen.getByTestId("classification-CLAIM").getAttribute("title")).toMatch(
      /not independently corroborated/i,
    );
  });
});

describe("ClassificationLegend", () => {
  it("documents all five classifications", () => {
    render(<ClassificationLegend />);
    for (const value of ["FACT", "CLAIM", "ANALYSIS", "OPINION", "UNKNOWN"]) {
      expect(screen.getByTestId(`classification-${value}`)).toBeInTheDocument();
    }
  });
});

describe("FetchDecisionTag", () => {
  it("shows when a page was blocked by robots.txt", () => {
    render(<FetchDecisionTag decision="BLOCKED_BY_ROBOTS" />);
    expect(screen.getByText("ROBOTS BLOCKED")).toBeInTheDocument();
  });

  it("shows when only the feed summary was used", () => {
    render(<FetchDecisionTag decision="DISABLED" />);
    expect(screen.getByText("SUMMARY ONLY")).toBeInTheDocument();
  });

  it("passes an unrecognised decision through rather than hiding it", () => {
    render(<FetchDecisionTag decision="SOMETHING_NEW" />);
    expect(screen.getByText("SOMETHING_NEW")).toBeInTheDocument();
  });
});
