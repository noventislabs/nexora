import type { ChannelRelevance, RelevanceStatus } from "@/lib/types";
import { Card, UNKNOWN_PLACEHOLDER } from "@/components/primitives";

const STATUS_STYLES: Record<RelevanceStatus, string> = {
  RELEVANT: "text-ok-500 bg-ok-500/10 border-ok-500/25",
  LOW_RELEVANCE: "text-base-400 bg-base-700/40 border-base-600",
  EXCLUDED: "text-danger-500 bg-danger-500/10 border-danger-500/25",
  INSUFFICIENT_DATA: "text-warn-500 bg-warn-500/10 border-warn-500/25",
};

const STATUS_LABELS: Record<RelevanceStatus, string> = {
  RELEVANT: "RELEVANT",
  LOW_RELEVANCE: "LOW RELEVANCE",
  EXCLUDED: "EXCLUDED",
  INSUFFICIENT_DATA: "INSUFFICIENT DATA",
};

export function RelevanceBadge({ status }: { status: RelevanceStatus }) {
  return (
    <span
      data-testid="relevance-badge"
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${
        STATUS_STYLES[status] ?? STATUS_STYLES.INSUFFICIENT_DATA
      }`}
    >
      {STATUS_LABELS[status] ?? STATUS_LABELS.INSUFFICIENT_DATA}
    </span>
  );
}

/**
 * Why this channel was, or was not, shown this trend.
 *
 * Every line here is a stored fact — a keyword that matched, a rule the operator
 * configured, a count that was measured. None of it is a model's opinion about an
 * audience, and the panel says so rather than leaving the reader to assume otherwise.
 */
export function RelevanceExplanation({
  relevance,
  channelName,
}: {
  relevance: ChannelRelevance;
  channelName?: string;
}) {
  const excluded = relevance.status === "EXCLUDED";

  return (
    <Card
      title={channelName ? `Relevance for ${channelName}` : "Relevance for this channel"}
      action={<RelevanceBadge status={relevance.status} />}
    >
      {excluded && (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3"
        >
          <p className="text-sm font-medium text-danger-500">
            Excluded by this channel&apos;s own rules. It is not ranked and will not be
            offered as evidence for a topic.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-base-300">
            {relevance.excluded_by_rules.map((rule, index) => (
              <li key={index} data-testid="exclusion-rule">
                <code className="font-mono text-danger-500">{rule.value}</code> —{" "}
                {rule.detail}{" "}
                <span className="text-base-500">({rule.rule.replace(/_/g, " ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-3 border-b border-base-800 pb-3 sm:grid-cols-4">
        <Figure
          label="Relevance"
          value={relevance.relevance_score}
          reason={
            excluded
              ? "Excluded items are not scored."
              : "Nothing configured to match against."
          }
        />
        <Figure
          label="Opportunity"
          value={relevance.score}
          reason={
            relevance.score_status === "EXCLUDED"
              ? "Excluded items are not scored."
              : "Needs both a measured signal and a known relevance."
          }
        />
        <div>
          <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">Sources</dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-base-100">
            {relevance.available_source_count}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">Freshness</dt>
          <dd className="mt-1 text-sm text-base-200">
            {relevance.freshness ?? UNKNOWN_PLACEHOLDER}
          </dd>
        </div>
      </dl>

      {relevance.matched_categories.length > 0 && (
        <Section title="Matched categories">
          <ul className="space-y-1.5">
            {relevance.matched_categories.map((entry) => (
              <li
                key={`${entry.category}-${entry.tier}`}
                data-testid={`matched-${entry.category}`}
                className="text-sm text-base-200"
              >
                <span className="font-medium">{entry.category}</span>{" "}
                <span className="text-xs text-base-500">({entry.tier})</span>
                <span className="ml-2 text-xs text-base-400">
                  matched: {entry.keywords.join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {relevance.matched_preferences.length > 0 && (
        <Section title="Matched preferred topics">
          <ul className="flex flex-wrap gap-1.5">
            {relevance.matched_preferences.map((entry) => (
              <li
                key={entry.preference}
                className="rounded-full border border-accent-600/40 bg-accent-600/10 px-2.5 py-0.5 text-xs text-accent-400"
              >
                {entry.preference}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Why">
        <ul className="space-y-1.5">
          {relevance.relevance_reasons.map((reason, index) => (
            <li key={index} data-testid="relevance-reason" className="text-xs text-base-400">
              {reason.detail}
            </li>
          ))}
        </ul>
      </Section>

      <p className="mt-4 border-t border-base-800 pt-3 text-[11px] text-base-500">
        Relevance is keyword matching against this channel&apos;s stored configuration.
        NEXORA does not model your audience and holds no demographic data about your
        viewers. The same trend can rank differently for another channel.
      </p>
    </Card>
  );
}

function Figure({
  label,
  value,
  reason,
}: {
  label: string;
  value: number | null;
  reason: string;
}) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">{label}</dt>
      <dd
        data-testid={`figure-${label.toLowerCase()}`}
        title={value === null ? reason : undefined}
        className={
          value === null
            ? "mt-1 font-mono text-sm text-base-500"
            : "mt-1 font-mono text-sm tabular-nums text-base-100"
        }
      >
        {value === null ? UNKNOWN_PLACEHOLDER : value}
      </dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <h3 className="mb-2 text-[10px] uppercase tracking-[0.12em] text-base-500">{title}</h3>
      {children}
    </div>
  );
}


/**
 * The one-line version, for a trend row in a list.
 *
 * It states the match or the exclusion in the operator's own words, so a person
 * scanning the list never has to wonder why an item is ranked where it is.
 */
export function RelevanceSummary({ relevance }: { relevance: ChannelRelevance }) {
  if (relevance.status === "EXCLUDED") {
    const values = relevance.excluded_by_rules.map((rule) => rule.value).join(", ");
    return (
      <p data-testid="relevance-summary" className="mt-2 text-[11px] text-danger-500">
        Excluded by this channel&apos;s rules: {values || "a configured rule"}.
      </p>
    );
  }

  if (relevance.status === "INSUFFICIENT_DATA") {
    return (
      <p data-testid="relevance-summary" className="mt-2 text-[11px] text-warn-500">
        {relevance.relevance_reasons[0]?.detail ??
          "Not enough configuration to judge this item for this channel."}
      </p>
    );
  }

  if (relevance.matched_categories.length === 0 && relevance.matched_preferences.length === 0) {
    return (
      <p data-testid="relevance-summary" className="mt-2 text-[11px] text-base-500">
        {relevance.relevance_reasons[0]?.detail ??
          "No match with this channel's configured content preferences."}
      </p>
    );
  }

  const matches = [
    ...relevance.matched_categories.map((entry) => entry.category),
    ...relevance.matched_preferences.map((entry) => entry.preference),
  ];
  return (
    <p data-testid="relevance-summary" className="mt-2 text-[11px] text-base-400">
      Matches this channel on <span className="text-base-200">{matches.join(", ")}</span>.
    </p>
  );
}
