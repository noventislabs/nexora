import type { CopyrightCheck, Preflight, PreflightGate, QualityCheck } from "@/lib/types";
import { Card, StatusPill } from "@/components/primitives";

const CHECK_STATUS_STYLES: Record<string, string> = {
  PASS: "text-ok-500 bg-ok-500/10 border-ok-500/25",
  WARN: "text-warn-500 bg-warn-500/10 border-warn-500/25",
  FAIL: "text-danger-500 bg-danger-500/10 border-danger-500/25",
  UNKNOWN: "text-base-400 bg-base-700/40 border-base-600",
};

export function CheckStatusBadge({ status }: { status: string }) {
  return (
    <span
      data-testid="check-status"
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${
        CHECK_STATUS_STYLES[status] ?? CHECK_STATUS_STYLES.UNKNOWN
      }`}
    >
      {status}
    </span>
  );
}

/**
 * One publishing gate.
 *
 * A failed non-blocking gate is a warning, and it says so — it is never dressed up as
 * a pass, and it never silently disappears.
 */
export function GateRow({ gate }: { gate: PreflightGate }) {
  const state = gate.passed ? "PASS" : gate.blocking ? "BLOCKED" : "WARNING";
  const tone = gate.passed
    ? "text-ok-500"
    : gate.blocking
      ? "text-danger-500"
      : "text-warn-500";

  return (
    <li
      data-testid={`gate-${gate.key}`}
      className="flex items-start gap-3 border-b border-base-800 py-2.5 last:border-0"
    >
      <span className={`mt-0.5 shrink-0 font-mono text-[10px] tracking-wide ${tone}`}>
        {state}
      </span>
      <div className="min-w-0">
        <p className="text-sm text-base-200">{gate.label}</p>
        <p className="mt-0.5 text-xs text-base-400">{gate.detail}</p>
      </div>
    </li>
  );
}

export function PreflightPanel({ preflight }: { preflight: Preflight }) {
  return (
    <Card
      title="Publishing preflight"
      action={
        <StatusPill
          status={preflight.can_publish ? "AVAILABLE" : "UNAVAILABLE"}
          label={preflight.can_publish ? "READY TO PUBLISH" : "BLOCKED"}
        />
      }
    >
      {preflight.blockers.length > 0 && (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3"
        >
          <p className="text-sm font-medium text-danger-500">
            {preflight.blockers.length} blocker{preflight.blockers.length === 1 ? "" : "s"} must
            be cleared before this video can be uploaded.
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-base-300">
            {preflight.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      )}

      {preflight.warnings.length > 0 && (
        <div className="mb-4 rounded-lg border border-warn-500/30 bg-warn-500/10 px-4 py-3">
          <p className="text-sm font-medium text-warn-500">
            Warnings — publishing is allowed, but these were not verified.
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-base-300">
            {preflight.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <ul>
        {preflight.gates.map((gate) => (
          <GateRow key={gate.key} gate={gate} />
        ))}
      </ul>
    </Card>
  );
}

export function QualityPanel({ quality }: { quality: QualityCheck }) {
  return (
    <Card
      title="Quality check"
      action={<CheckStatusBadge status={quality.status} />}
    >
      <p className="mb-3 text-xs text-base-500">
        Every item below is a deterministic rule evaluated against the produced files. No
        model was asked to score this work.
      </p>
      <ul>
        {quality.checks.map((check) => (
          <GateRow key={check.key} gate={check} />
        ))}
      </ul>
    </Card>
  );
}

export function CopyrightPanel({ copyright }: { copyright: CopyrightCheck }) {
  return (
    <Card title="Copyright check" action={<CheckStatusBadge status={copyright.status} />}>
      <dl className="mb-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs text-base-500">Risk level</dt>
          <dd className="text-base-200">{copyright.risk_level}</dd>
        </div>
        <div>
          <dt className="text-xs text-base-500">Unknown licences</dt>
          <dd className="font-mono tabular-nums text-base-200">
            {copyright.unknown_license_count}
          </dd>
        </div>
      </dl>
      {copyright.findings.length === 0 ? (
        <p className="text-sm text-base-400">
          No asset in this project has a prohibited or unknown licence.
        </p>
      ) : (
        <ul className="space-y-2">
          {copyright.findings.map((finding, index) => (
            <li key={index} className="rounded-lg border border-base-800 px-3 py-2">
              <p className="text-sm text-base-200">{finding.label}</p>
              <p className="mt-0.5 text-xs text-base-400">{finding.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
