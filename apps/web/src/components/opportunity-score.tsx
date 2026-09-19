"use client";

import { useState } from "react";
import type { ScoreBreakdown } from "@/lib/types";
import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";

/**
 * Renders the Opportunity Score.
 *
 * Two rules this component exists to enforce:
 * 1. An unavailable score shows `—` with its reason, never a number.
 * 2. The breakdown is always one click away, so the figure is never a black box.
 */
export function OpportunityScore({
  score,
  breakdown,
  compact = false,
}: {
  score: number | null;
  breakdown: ScoreBreakdown | null;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);

  if (score === null) {
    const reason =
      breakdown?.unavailable_reason ??
      "This item could not be scored from the data that was collected.";
    return (
      <span
        data-testid="opportunity-score"
        title={reason}
        className="inline-flex items-center gap-1.5 rounded-lg border border-base-700 bg-base-850 px-2.5 py-1"
      >
        <span className="font-mono text-sm text-base-500">{UNKNOWN_PLACEHOLDER}</span>
        <span className="text-[10px] uppercase tracking-wider text-base-500">
          not scorable
        </span>
      </span>
    );
  }

  const tone =
    score >= 75
      ? "border-ok-500/30 bg-ok-500/10 text-ok-500"
      : score >= 45
        ? "border-warn-500/30 bg-warn-500/10 text-warn-500"
        : "border-base-600 bg-base-800 text-base-300";

  return (
    <div className="inline-block">
      <button
        type="button"
        data-testid="opportunity-score"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 transition-colors ${tone}`}
      >
        <span className="font-mono text-sm font-semibold tabular-nums">{score}</span>
        <span className="text-[10px] uppercase tracking-wider opacity-70">/100</span>
        {!compact && (
          <span className="ml-1 text-[10px] opacity-70">{open ? "hide" : "why"}</span>
        )}
      </button>

      {open && breakdown && (
        <div className="mt-2 w-full max-w-md rounded-lg border border-base-700 bg-base-900 p-3">
          <p className="text-[11px] leading-relaxed text-base-400">{breakdown.method}</p>
          <ul className="mt-2.5 space-y-2">
            {breakdown.components.map((component) => (
              <li key={component.key} className="border-b border-base-800 pb-2 last:border-0">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-base-200">{component.label}</span>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-base-300">
                    {component.available ? (
                      <>
                        {component.value}
                        <span className="ml-1.5 text-base-500">
                          ×{component.weight.toFixed(2)}
                        </span>
                      </>
                    ) : (
                      <span className="text-base-500">{UNKNOWN_PLACEHOLDER} unavailable</span>
                    )}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-base-500">
                  {component.basis}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] text-base-500">
            Computed over {Math.round(breakdown.available_weight * 100)}% of the total weight.
            This is not a prediction of views, virality or revenue.
          </p>
        </div>
      )}
    </div>
  );
}

export function CompetitionBadge({ level }: { level: string }) {
  const tone =
    level === "low"
      ? "text-ok-500"
      : level === "medium"
        ? "text-warn-500"
        : level === "high"
          ? "text-danger-500"
          : "text-base-500";
  return (
    <span className={`text-[11px] uppercase tracking-wider ${tone}`}>
      competition: {level === "unknown" ? UNKNOWN_PLACEHOLDER : level}
    </span>
  );
}
