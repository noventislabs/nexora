"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Sections named by the product spec, in workflow order. */
export const SECTIONS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/trends", label: "Trends" },
  { href: "/dashboard/ideas", label: "Ideas" },
  { href: "/dashboard/research", label: "Research" },
  { href: "/dashboard/scripts", label: "Scripts" },
  { href: "/dashboard/production", label: "Production" },
  { href: "/dashboard/publishing", label: "Publishing" },
  { href: "/dashboard/analytics", label: "Analytics" },
  { href: "/dashboard/automation", label: "Automation" },
  { href: "/dashboard/channels", label: "Channels" },
  { href: "/dashboard/settings", label: "Settings" },
  { href: "/dashboard/logs", label: "Logs" },
] as const;

export function SideNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Sections" className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
      {SECTIONS.map((section) => {
        const active =
          section.href === "/dashboard"
            ? pathname === "/dashboard"
            : pathname.startsWith(section.href);
        return (
          <Link
            key={section.href}
            href={section.href}
            aria-current={active ? "page" : undefined}
            className={`shrink-0 rounded-lg px-3 py-2 text-sm transition-colors ${
              active
                ? "bg-accent-600/15 text-accent-400"
                : "text-base-400 hover:bg-base-800 hover:text-base-200"
            }`}
          >
            {section.label}
          </Link>
        );
      })}
    </nav>
  );
}
