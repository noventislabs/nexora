import type { BaselineResult, VideoComparison } from "@/lib/types";
import { Card, UNKNOWN_PLACEHOLDER } from "@/components/primitives";

/**
 * A channel's own median for one metric, or a stated reason there isn't one.
 *
 * The sample size and observation period are rendered alongside the number rather
 * than tucked into a tooltip: a median of five videos and a median of five hundred
 * support very different decisions, and the reader needs to see which they have.
 */
export function BaselineFigure({
  metric,
  baseline,
}: {
  metric: string;
  baseline: BaselineResult;
}) {
  const label = metric.replace(/_/g, " ");

  if (baseline.status === "INSUFFICIENT_DATA") {
    return (
      <div
        data-testid={`baseline-${metric}`}
        className="rounded-lg border border-base-800 bg-base-850/60 px-3 py-2.5"
      >
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-sm text-base-300">{label}</span>
          <span className="font-mono text-sm text-base-500">{UNKNOWN_PLACEHOLDER}</span>
        </div>
        <p className="mt-1 text-xs text-warn-500">{baseline.reason}</p>
      </div>
    );
  }

  return (
    <div
      data-testid={`baseline-${metric}`}
      className="rounded-lg border border-base-800 bg-base-850/60 px-3 py-2.5"
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm text-base-300">{label}</span>
        <span className="font-mono text-sm tabular-nums text-base-100">
          {baseline.median.toLocaleString()}
        </span>
      </div>
      <p className="mt-1 text-xs text-base-500">
        median of <span data-testid={`sample-${metric}`}>{baseline.sample_size}</span> videos
        over {baseline.observation_period.days} days · range{" "}
        {baseline.range.min.toLocaleString()}–{baseline.range.max.toLocaleString()}
      </p>
    </div>
  );
}

/**
 * One video measured against its own channel's baseline.
 *
 * The wording is a measurement, and the factors are labelled as attributes the video
 * happens to have — never as reasons it performed as it did. NEXORA can measure a
 * difference; it cannot isolate a cause, and this panel does not pretend otherwise.
 */
export function ComparisonPanel({ comparison }: { comparison: VideoComparison }) {
  const baseline = comparison.compared_against;

  return (
    <Card title={`Compared on ${comparison.metric.replace(/_/g, " ")}`}>
      <p data-testid="observation" className="text-sm text-base-200">
        {comparison.observation}
      </p>

      <dl className="mt-3 grid grid-cols-3 gap-3 border-t border-base-800 pt-3">
        <div>
          <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">This video</dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-base-100">
            {comparison.value.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">
            Channel median
          </dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-base-100">
            {baseline.median.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-[0.12em] text-base-500">Sample</dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-base-100">
            {baseline.sample_size}
          </dd>
        </div>
      </dl>

      <p className="mt-2 text-[11px] text-base-500">{baseline.basis}</p>

      {comparison.possible_factors.length > 0 && (
        <div className="mt-4 border-t border-base-800 pt-3">
          <h3 className="mb-2 text-[10px] uppercase tracking-[0.12em] text-base-500">
            Possible contributing factors
          </h3>
          <ul className="space-y-1">
            {comparison.possible_factors.map((factor) => (
              <li
                key={factor.attribute}
                data-testid={`factor-${factor.attribute}`}
                className="text-xs text-base-400"
              >
                {factor.note}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p
        data-testid="not-a-cause"
        className="mt-3 border-t border-base-800 pt-3 text-[11px] text-base-500"
      >
        {comparison.not_a_cause}
      </p>
    </Card>
  );
}
