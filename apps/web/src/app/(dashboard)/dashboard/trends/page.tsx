"use client";

import { useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  StatusPill,
  UNKNOWN_PLACEHOLDER,
  formatAge,
} from "@/components/primitives";
import { OpportunityScore } from "@/components/opportunity-score";
import { RelevanceBadge, RelevanceSummary } from "@/components/relevance";
import type {
  Paged,
  ScanResponse,
  ScoringModel,
  Trend,
  TrendSource,
  TrendSourcesResponse,
} from "@/lib/types";

const PAGE_SIZE = 20;

export default function TrendsPage() {
  const [offset, setOffset] = useState(0);
  const [sourceKind, setSourceKind] = useState<string>("");
  const [scoredOnly, setScoredOnly] = useState(false);
  const [scan, setScan] = useState<{ running: boolean; result: ScanResponse | null; error: string | null }>(
    { running: false, result: null, error: null },
  );

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (sourceKind) query.set("source_kind", sourceKind);
  if (scoredOnly) query.set("scored_only", "true");

  const trends = useApi<Paged<Trend>>(`/api/trends?${query.toString()}`);
  const sources = useApi<TrendSourcesResponse>("/api/trends/sources");

  async function runScan() {
    setScan({ running: true, result: null, error: null });
    try {
      const result = await apiFetch<ScanResponse>("/api/trends/scan", {
        method: "POST",
        body: { background: false, force: true },
      });
      setScan({ running: false, result, error: null });
      trends.refresh();
      sources.refresh();
    } catch (error) {
      setScan({
        running: false,
        result: null,
        error: error instanceof ApiError ? error.message : "The scan could not be started.",
      });
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-base-100">Trends</h1>
          <p className="mt-0.5 text-sm text-base-400">
            Observations collected from your configured sources. Metrics a source does not
            publish are shown as {UNKNOWN_PLACEHOLDER}, never as zero.
          </p>
        </div>
        <Button variant="primary" onClick={runScan} disabled={scan.running}>
          {scan.running ? "Scanning…" : "Scan now"}
        </Button>
      </header>

      {scan.error && <ErrorNotice message={scan.error} />}
      {scan.result && <ScanSummary result={scan.result} />}

      <SourcesPanel state={sources} onChanged={() => { sources.refresh(); trends.refresh(); }} />

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={sourceKind}
          onChange={(event) => { setSourceKind(event.target.value); setOffset(0); }}
          className="rounded-lg border border-base-600 bg-base-850 px-3 py-1.5 text-sm text-base-100"
        >
          <option value="">All sources</option>
          <option value="rss">RSS</option>
          <option value="youtube_data_api">YouTube</option>
          <option value="reddit">Reddit</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-base-400">
          <input
            type="checkbox"
            checked={scoredOnly}
            onChange={(event) => { setScoredOnly(event.target.checked); setOffset(0); }}
            className="h-4 w-4 accent-cyan-500"
          />
          Scored only
        </label>
        <Button onClick={trends.refresh} className="ml-auto">Refresh</Button>
      </div>

      {trends.status === "loading" && <Loading label="Loading trends" />}
      {trends.status === "error" && <ErrorNotice message={trends.error.message} />}
      {trends.status === "ready" && trends.data.total === 0 && (
        <EmptyState
          title="No trend items yet"
          description="Nothing has been collected from your sources. Add a source and run a scan — the list stays empty until real data arrives."
        />
      )}
      {trends.status === "ready" && trends.data.total > 0 && (
        <>
          <ul className="space-y-3">
            {trends.data.items.map((trend) => (
              <TrendRow key={trend.id} trend={trend} />
            ))}
          </ul>
          <Pagination total={trends.data.total} offset={offset} onChange={setOffset} />
        </>
      )}

      <ScoringModelCard />
    </div>
  );
}

function TrendRow({ trend }: { trend: Trend }) {
  const metrics = Object.entries(trend.engagement);
  return (
    <li className="rounded-xl border border-base-700 bg-base-900/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium text-base-100">
            {trend.url ? (
              <a
                href={trend.url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="hover:text-accent-400 hover:underline"
              >
                {trend.title}
              </a>
            ) : (
              trend.title
            )}
          </h3>
          {trend.summary && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-base-400">{trend.summary}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <OpportunityScore
            score={trend.opportunity_score}
            breakdown={trend.signal_breakdown}
          />
          {trend.relevance && <RelevanceBadge status={trend.relevance.status} />}
        </div>
      </div>

      {trend.relevance && <RelevanceSummary relevance={trend.relevance} />}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-base-500">
        <span className="font-mono text-base-400">{trend.source.name}</span>
        <span className="uppercase tracking-wider">{trend.source.kind.replace(/_/g, " ")}</span>
        {trend.category && <span>{trend.category}</span>}
        {trend.region && <span>{trend.region}</span>}
        <FreshnessTag trend={trend} />
        <span>discovered {formatAge(ageOf(trend.discovered_at))}</span>
        {trend.scope === "shared" && <span title="Ingested once for this account and ranked separately by each channel.">shared source</span>}
        {trend.corroboration_count > 1 && (
          <span className="text-accent-400">
            {trend.corroboration_count} sources carry this story
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {metrics.length === 0 ? (
          <span className="text-base-600">
            This source publishes no engagement metrics {UNKNOWN_PLACEHOLDER}
          </span>
        ) : (
          metrics.map(([key, value]) => (
            <span key={key} className="font-mono text-base-400">
              {key.replace(/_/g, " ")}:{" "}
              <span className="text-base-200 tabular-nums">{value.toLocaleString()}</span>
            </span>
          ))
        )}
      </div>
    </li>
  );
}

function FreshnessTag({ trend }: { trend: Trend }) {
  const { state, basis } = trend.freshness;
  if (state === "UNKNOWN") {
    return <span className="text-base-600">freshness {UNKNOWN_PLACEHOLDER}</span>;
  }
  const tone = state === "FRESH" ? "text-ok-500" : "text-warn-500";
  return (
    <span className={tone} title={`Measured from ${basis}`}>
      {state}
    </span>
  );
}

function ScanSummary({ result }: { result: ScanResponse }) {
  if (result.mode === "queued") {
    return (
      <Card title="Scan queued">
        <p className="text-sm text-base-300">
          Job <code className="font-mono text-accent-400">{result.job_id}</code> is queued. Track it
          under Logs.
        </p>
      </Card>
    );
  }
  return (
    <Card title={`Scan complete · ${result.duration_seconds}s`}>
      <p className="text-sm text-base-300">
        Fetched {result.fetched}, stored {result.stored} new, {result.duplicates} already known.
      </p>
      <ul className="mt-3 space-y-1.5">
        {result.sources.map((source) => (
          <li key={source.source_id} className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs text-base-300">{source.source_name}</span>
            <span className="flex items-center gap-2">
              <span className="text-[11px] text-base-500">
                {source.fetched} fetched · {source.stored} new
              </span>
              <StatusPill
                status={
                  source.status === "SUCCESS"
                    ? "HEALTHY"
                    : source.status === "NOT_CONFIGURED"
                      ? "NOT CONFIGURED"
                      : source.status === "SKIPPED"
                        ? "NOT CONNECTED"
                        : "UNHEALTHY"
                }
                label={source.status}
              />
            </span>
            {source.error && (
              <p className="w-full text-[11px] text-base-500">{source.error}</p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SourcesPanel({
  state,
  onChanged,
}: {
  state: ReturnType<typeof useApi<TrendSourcesResponse>>;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function seed() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/trends/sources/seed-defaults", { method: "POST" });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the default sources.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(source: TrendSource) {
    setBusy(true);
    try {
      await apiFetch(`/api/trends/sources/${source.id}`, {
        method: "PATCH",
        body: { enabled: !source.enabled },
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the source.");
    } finally {
      setBusy(false);
    }
  }

  if (state.status === "loading") return <Card title="Sources"><Loading /></Card>;
  if (state.status === "error")
    return (
      <Card title="Sources">
        <ErrorNotice message={state.error.message} />
      </Card>
    );

  const { items, providers } = state.data;

  return (
    <Card
      title="Sources"
      action={
        items.length === 0 ? (
          <Button variant="primary" onClick={seed} disabled={busy}>
            Add default RSS feeds
          </Button>
        ) : undefined
      }
    >
      {error && <ErrorNotice message={error} />}

      <div className="mb-4 space-y-1.5">
        <p className="text-[11px] uppercase tracking-[0.12em] text-base-500">Provider status</p>
        {Object.entries(providers).map(([key, availability]) => (
          <div key={key} className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs uppercase tracking-wide text-base-300">
              {key.replace(/_/g, " ")}
            </span>
            <span title={availability.detail}>
              <StatusPill status={availability.status} />
            </span>
            {availability.missing_settings.length > 0 && (
              <p className="w-full text-[11px] text-base-500">
                Set{" "}
                <code className="font-mono text-accent-400">
                  {availability.missing_settings.join(", ")}
                </code>{" "}
                in the server environment to enable it.
              </p>
            )}
          </div>
        ))}
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-base-400">
          No sources configured. RSS needs no credentials, so the default feeds work immediately.
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((source) => (
            <li
              key={source.id}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-base-800 pb-2 last:border-0 last:pb-0"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm text-base-200">{source.name}</span>
                <span className="text-[11px] text-base-500">
                  {source.kind.replace(/_/g, " ")} · every {source.min_interval_minutes}m ·{" "}
                  {source.last_run_at
                    ? `last run ${formatAge(ageOf(source.last_run_at))}`
                    : "never run"}
                  {source.last_item_count !== null && ` · ${source.last_item_count} items`}
                </span>
                {source.last_error && (
                  <span className="mt-0.5 block text-[11px] text-danger-500">{source.last_error}</span>
                )}
              </span>
              <span className="flex items-center gap-2">
                <StatusPill status={source.availability.status} />
                <Button variant="ghost" disabled={busy} onClick={() => toggle(source)}>
                  {source.enabled ? "Disable" : "Enable"}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ScoringModelCard() {
  const model = useApi<ScoringModel>("/api/trends/scoring-model");
  const [open, setOpen] = useState(false);

  if (model.status !== "ready") return null;

  return (
    <Card
      title="How the Opportunity Score is calculated"
      action={
        <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      <p className="text-sm text-base-300">{model.data.not_a_prediction}</p>
      {open && (
        <>
          <p className="mt-3 font-mono text-[11px] leading-relaxed text-base-400">
            {model.data.formula}
          </p>
          <p className="mt-2 text-[11px] text-base-500">{model.data.unavailable_rule}</p>
          <ul className="mt-3 space-y-2">
            {Object.entries(model.data.components).map(([key, description]) => (
              <li key={key} className="border-b border-base-800 pb-2 last:border-0">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-base-200">{key.replace(/_/g, " ")}</span>
                  <span className="font-mono text-xs text-base-400">
                    weight {model.data.weights[key]?.toFixed(2)}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] text-base-500">{description}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function Pagination({
  total,
  offset,
  onChange,
}: {
  total: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="flex items-center justify-between gap-3 text-xs text-base-400">
      <span>
        Page {page} of {pages} · {total} items
      </span>
      <div className="flex gap-2">
        <Button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </Button>
        <Button disabled={page >= pages} onClick={() => onChange(offset + PAGE_SIZE)}>
          Next
        </Button>
      </div>
    </div>
  );
}

function ageOf(iso: string | null): number | null {
  if (!iso) return null;
  return (Date.now() - new Date(iso).getTime()) / 1000;
}
