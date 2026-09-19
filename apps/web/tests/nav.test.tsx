import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SECTIONS, SideNav } from "@/components/nav";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard/automation" }));

describe("SideNav", () => {
  it("exposes every section named by the product spec", () => {
    const labels = SECTIONS.map((section) => section.label);
    expect(labels).toEqual([
      "Overview",
      "Trends",
      "Ideas",
      "Research",
      "Scripts",
      "Production",
      "Publishing",
      "Analytics",
      "Automation",
      "Channels",
      "Settings",
      "Logs",
    ]);
  });

  it("marks the active section for assistive technology", () => {
    render(<SideNav />);
    expect(screen.getByRole("link", { name: "Automation" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });
});
