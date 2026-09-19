import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Metric, NotConfigured, StatusPill, formatAge, UNKNOWN_PLACEHOLDER } from "@/components/primitives";

describe("Metric", () => {
  it("renders a real value when the backend has one", () => {
    render(<Metric label="Views" metric={{ value: 12345, available: true, unavailable_reason: null }} />);
    expect(screen.getByTestId("metric-views")).toHaveTextContent("12,345");
  });

  it("renders a dash, never 0, when the value is unknown", () => {
    render(
      <Metric
        label="Subscribers"
        metric={{
          value: null,
          available: false,
          unavailable_reason: "YouTube API not connected",
        }}
      />,
    );
    const cell = screen.getByTestId("metric-subscribers");
    expect(cell).toHaveTextContent(UNKNOWN_PLACEHOLDER);
    expect(cell.textContent).not.toContain("0");
    expect(cell).toHaveAttribute("title", "YouTube API not connected");
  });

  it("renders a genuine measured zero as 0", () => {
    render(<Metric label="Published" metric={{ value: 0, available: true, unavailable_reason: null }} />);
    expect(screen.getByTestId("metric-published")).toHaveTextContent("0");
  });

  it("treats available:false as unknown even if a value leaks through", () => {
    render(
      <Metric
        label="Revenue"
        metric={{ value: 127, available: false, unavailable_reason: "Monetary scope not granted" }}
      />,
    );
    const cell = screen.getByTestId("metric-revenue");
    expect(cell).toHaveTextContent(UNKNOWN_PLACEHOLDER);
    expect(cell.textContent).not.toContain("127");
  });
});

describe("StatusPill", () => {
  it.each(["HEALTHY", "NOT CONFIGURED", "NOT CONNECTED", "UNAVAILABLE"] as const)(
    "renders %s verbatim",
    (status) => {
      render(<StatusPill status={status} />);
      expect(screen.getByTestId("status-pill")).toHaveTextContent(status);
    },
  );
});

describe("NotConfigured", () => {
  it("names the missing environment variables", () => {
    render(
      <NotConfigured
        feature="Voice provider"
        detail="VOICE PROVIDER NOT CONFIGURED."
        missing={["VOICE_PROVIDER", "ELEVENLABS_API_KEY"]}
      />,
    );
    expect(screen.getByTestId("not-configured")).toHaveTextContent("VOICE_PROVIDER, ELEVENLABS_API_KEY");
    expect(screen.getByTestId("status-pill")).toHaveTextContent("NOT CONFIGURED");
  });
});

describe("formatAge", () => {
  it("returns the placeholder when the age is unknown", () => {
    expect(formatAge(null)).toBe(UNKNOWN_PLACEHOLDER);
  });

  it("formats recent ages in human units", () => {
    expect(formatAge(30)).toBe("30 seconds ago");
    expect(formatAge(720)).toBe("12 minutes ago");
    expect(formatAge(7200)).toBe("2 hours ago");
  });
});
