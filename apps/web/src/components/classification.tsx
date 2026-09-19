import type { Classification } from "@/lib/types";

/**
 * The five-way classification the research engine is constrained to.
 *
 * These are visually distinct on purpose: the difference between a FACT and a CLAIM is
 * the most important thing on the research screen, and a viewer of this UI should never
 * have to hunt for it.
 */
const STYLES: Record<Classification, { style: string; meaning: string }> = {
  FACT: {
    style: "text-ok-500 border-ok-500/30 bg-ok-500/10",
    meaning: "Directly stated in a source and not disputed by another source.",
  },
  CLAIM: {
    style: "text-accent-400 border-accent-600/30 bg-accent-600/10",
    meaning: "Asserted by a source but not independently corroborated here.",
  },
  ANALYSIS: {
    style: "text-base-200 border-base-600 bg-base-800",
    meaning: "An inference drawn from the sources, not a statement they make.",
  },
  OPINION: {
    style: "text-warn-500 border-warn-500/30 bg-warn-500/10",
    meaning: "Someone's judgement, attributed to whoever holds it.",
  },
  UNKNOWN: {
    style: "text-base-500 border-base-700 bg-base-850",
    meaning: "Relevant, but the sources do not settle it.",
  },
};

export function ClassificationBadge({ value }: { value: Classification }) {
  const entry = STYLES[value] ?? STYLES.UNKNOWN;
  return (
    <span
      data-testid={`classification-${value}`}
      title={entry.meaning}
      className={`inline-block shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-wider ${entry.style}`}
    >
      {value}
    </span>
  );
}

export function ClassificationLegend() {
  return (
    <dl className="grid gap-1.5 sm:grid-cols-2">
      {(Object.keys(STYLES) as Classification[]).map((key) => (
        <div key={key} className="flex items-start gap-2">
          <dt>
            <ClassificationBadge value={key} />
          </dt>
          <dd className="text-[11px] leading-relaxed text-base-500">{STYLES[key].meaning}</dd>
        </div>
      ))}
    </dl>
  );
}

const FETCH_STYLES: Record<string, { label: string; style: string }> = {
  ALLOWED: { label: "FETCHED", style: "text-ok-500" },
  BLOCKED_BY_ROBOTS: { label: "ROBOTS BLOCKED", style: "text-warn-500" },
  DISABLED: { label: "SUMMARY ONLY", style: "text-base-500" },
  NOT_ATTEMPTED: { label: "SUMMARY ONLY", style: "text-base-500" },
  FAILED: { label: "FETCH FAILED", style: "text-danger-500" },
};

export function FetchDecisionTag({ decision }: { decision: string }) {
  const entry = FETCH_STYLES[decision] ?? { label: decision, style: "text-base-500" };
  return (
    <span className={`font-mono text-[10px] tracking-wider ${entry.style}`}>{entry.label}</span>
  );
}
