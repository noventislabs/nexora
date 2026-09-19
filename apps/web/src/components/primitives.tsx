import type { ReactNode } from "react";
import type { MaybeMetric, ProviderStatus } from "@/lib/types";

/** The single place that decides how an unknown value is rendered. */
export const UNKNOWN_PLACEHOLDER = "—";

const STATUS_STYLES: Record<ProviderStatus, string> = {
  HEALTHY: "text-ok-500 bg-ok-500/10 border-ok-500/25",
  AVAILABLE: "text-ok-500 bg-ok-500/10 border-ok-500/25",
  CONNECTED: "text-ok-500 bg-ok-500/10 border-ok-500/25",
  DEGRADED: "text-warn-500 bg-warn-500/10 border-warn-500/25",
  UNHEALTHY: "text-danger-500 bg-danger-500/10 border-danger-500/25",
  UNAVAILABLE: "text-danger-500 bg-danger-500/10 border-danger-500/25",
  "NOT CONNECTED": "text-base-400 bg-base-700/40 border-base-600",
  "NOT CONFIGURED": "text-base-400 bg-base-700/40 border-base-600",
};

export function StatusPill({ status, label }: { status: ProviderStatus; label?: string }) {
  return (
    <span
      data-testid="status-pill"
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide ${STATUS_STYLES[status] ?? STATUS_STYLES["NOT CONFIGURED"]}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {label ?? status}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-base-700 bg-base-900/70 p-4 sm:p-5 ${className}`}
    >
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          {title && (
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-base-400">
              {title}
            </h2>
          )}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/**
 * Renders a metric the backend may not have.
 *
 * A `null` value is shown as `—` with its reason as the tooltip. This component is the
 * reason no screen can accidentally display a fabricated zero.
 */
export function Metric({ label, metric }: { label: string; metric: MaybeMetric }) {
  const known = metric.available && metric.value !== null;
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-base-800 py-2 last:border-0">
      <span className="text-sm text-base-300">{label}</span>
      <span
        data-testid={`metric-${label.toLowerCase().replace(/\s+/g, "-")}`}
        title={known ? undefined : (metric.unavailable_reason ?? "Data unavailable")}
        className={
          known
            ? "font-mono text-sm tabular-nums text-base-100"
            : "font-mono text-sm text-base-500"
        }
      >
        {known ? metric.value!.toLocaleString() : UNKNOWN_PLACEHOLDER}
      </span>
    </div>
  );
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-base-800 bg-base-850/60 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-[0.12em] text-base-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-base-100">{value}</div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-base-700 px-5 py-10 text-center">
      <p className="text-sm font-medium text-base-200">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-base-400">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Shown when a feature needs credentials that this deployment does not have. */
export function NotConfigured({
  feature,
  detail,
  missing = [],
}: {
  feature: string;
  detail?: string;
  missing?: string[];
}) {
  return (
    <div
      data-testid="not-configured"
      className="rounded-lg border border-base-700 bg-base-850/60 px-5 py-6"
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status="NOT CONFIGURED" />
        <span className="text-sm font-medium text-base-200">{feature}</span>
      </div>
      {detail && <p className="mt-2 text-sm text-base-400">{detail}</p>}
      {missing.length > 0 && (
        <p className="mt-3 text-xs text-base-500">
          Missing configuration:{" "}
          <code className="font-mono text-accent-400">{missing.join(", ")}</code>
        </p>
      )}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-500"
    >
      {message}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-6 text-sm text-base-400" role="status">
      <span className="h-2 w-2 animate-pulse rounded-full bg-accent-500" aria-hidden />
      {label}…
    </div>
  );
}

export function Button({
  children,
  variant = "default",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "ghost";
}) {
  const styles = {
    default: "border-base-600 bg-base-800 text-base-100 hover:bg-base-700",
    primary: "border-accent-600 bg-accent-600/15 text-accent-400 hover:bg-accent-600/25",
    danger: "border-danger-500/40 bg-danger-500/10 text-danger-500 hover:bg-danger-500/20",
    ghost: "border-transparent bg-transparent text-base-300 hover:bg-base-800",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return UNKNOWN_PLACEHOLDER;
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} seconds ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}
