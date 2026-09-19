import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  LicenseBadge,
  TimingBadge,
  formatBytes,
  formatDuration,
} from "@/components/license-badge";
import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";

describe("LicenseBadge", () => {
  it("shows a permitted licence as cleared", () => {
    render(<LicenseBadge status="PERMITTED" licenseType="creator_owned" />);
    const badge = screen.getByTestId("license-badge");
    expect(badge).toHaveTextContent("PERMITTED");
    expect(badge.getAttribute("title")).toMatch(/cleared for use/i);
  });

  it("warns that an unknown licence blocks autonomous publishing", () => {
    render(<LicenseBadge status="LICENSE UNKNOWN" />);
    const badge = screen.getByTestId("license-badge");
    expect(badge).toHaveTextContent("LICENSE UNKNOWN");
    expect(badge.getAttribute("title")).toMatch(/blocks autonomous publishing/i);
  });

  it("treats an unrecognised status as unknown rather than permitted", () => {
    render(<LicenseBadge status="SOMETHING_ELSE" />);
    expect(screen.getByTestId("license-badge").getAttribute("title")).toMatch(
      /blocks autonomous publishing/i,
    );
  });

  it("styles permitted and unknown differently", () => {
    const { container: permitted } = render(<LicenseBadge status="PERMITTED" />);
    const { container: unknown } = render(<LicenseBadge status="LICENSE UNKNOWN" />);
    expect(permitted.firstElementChild?.className).not.toBe(
      unknown.firstElementChild?.className,
    );
  });
});

describe("TimingBadge", () => {
  it("marks estimated timing as estimated", () => {
    render(<TimingBadge source="estimated" />);
    const badge = screen.getByTestId("timing-badge");
    expect(badge).toHaveTextContent("TIMING ESTIMATED");
    expect(badge.getAttribute("title")).toMatch(/approximate/i);
  });

  it("marks provider timing as measured", () => {
    render(<TimingBadge source="provider" />);
    const badge = screen.getByTestId("timing-badge");
    expect(badge).toHaveTextContent("TIMING MEASURED");
    expect(badge.getAttribute("title")).toMatch(/character-level alignment/i);
  });

  it("renders a dash when the timing source is unknown", () => {
    render(<TimingBadge source={null} />);
    expect(screen.getByText(`TIMING ${UNKNOWN_PLACEHOLDER}`)).toBeInTheDocument();
  });
});

describe("formatters", () => {
  it("renders a dash rather than zero for unknown sizes and durations", () => {
    expect(formatBytes(null)).toBe(UNKNOWN_PLACEHOLDER);
    expect(formatBytes(undefined)).toBe(UNKNOWN_PLACEHOLDER);
    expect(formatDuration(null)).toBe(UNKNOWN_PLACEHOLDER);
  });

  it("renders a genuine zero as zero", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatDuration(0)).toBe("0s");
  });

  it("formats real values", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatDuration(95)).toBe("1m 35s");
    expect(formatDuration(42)).toBe("42s");
  });
});
