"use client";

import { useState } from "react";
import Link from "next/link";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  NotConfigured,
  UNKNOWN_PLACEHOLDER,
  formatAge,
} from "@/components/primitives";
import { CompetitionBadge, OpportunityScore } from "@/components/opportunity-score";
import type { Capabilities, Paged, TopicCandidate } from "@/lib/types";

const PAGE_SIZE = 20;
const STATUSES = ["", "proposed", "approved", "saved", "rejected"] as const;

export default function IdeasPage() {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<string>("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (status) query.set("status", status);

  const topics = useApi<Paged<TopicCandidate>>(`/api/topics?${query.toString()}`);
  const capabilities = useApi<Capabilities>("/api/system/capabilities");

  const llm = capabilities.status === "ready" ? capabilities.data.llm : null;
  const llmReady = llm ? llm.status === "AVAILABLE" || llm.status === "HEALTHY" : false;

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      await apiFetch("/api/topics/generate", {
        method: "POST",
        body: { count: 6, background: false },
      });
      topics.refresh();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Generation failed."));
    } finally {
      setGenerating(false);
    }
  }

  async function decide(id: string, decision: "approved" | "rejected" | "saved") {
    setError(null);
    try {
      await apiFetch(`/api/topics/${id}/decision`, {
        method: "POST",
        body: { status: decision },
      });
      topics.refresh();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Could not record the decision."));
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-base-100">Ideas</h1>
          <p className="mt-0.5 text-sm text-base-400">
            Topic candidates, each grounded in trend items that were actually collected.
          </p>
        </div>
        {llmReady && (
          <Button variant="primary" onClick={generate} disabled={generating}>
            {generating ? "Generating…" : "Generate topics"}
          </Button>
        )}
      </header>

      {llm && !llmReady && (
        <NotConfigured
          feature="Topic generation"
          detail={`${llm.detail} Until a provider is configured, NEXORA will not produce topics — it does not invent them locally.`}
          missing={llm.missing_settings}
        />
      )}

      {error && <ErrorNotice message={errorMessage(error)} />}

      <div className="flex flex-wrap items-center gap-2">
        {STATUSES.map((value) => (
          <button
            key={value || "all"}
            onClick={() => { setStatus(value); setOffset(0); }}
            aria-pressed={status === value}
            className={`rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
              status === value
                ? "bg-accent-600/15 text-accent-400"
                : "text-base-400 hover:bg-base-800 hover:text-base-200"
            }`}
          >
            {value || "all"}
          </button>
        ))}
        <Button onClick={topics.refresh} className="ml-auto">Refresh</Button>
      </div>

      {topics.status === "loading" && <Loading label="Loading ideas" />}
      {topics.status === "error" && <ErrorNotice message={topics.error.message} />}
      {topics.status === "ready" && topics.data.total === 0 && (
        <EmptyState
          title="No topic candidates yet"
          description={
            llmReady
              ? "Run a trend scan, then generate topics. Every candidate must cite the trend items it came from."
              : "Topic generation needs an LLM provider. Collected trends are still visible under Trends."
          }
          action={
            <Link href="/dashboard/trends">
              <Button>Go to Trends</Button>
            </Link>
          }
        />
      )}
      {topics.status === "ready" && topics.data.total > 0 && (
        <>
          <ul className="space-y-4">
            {topics.data.items.map((topic) => (
              <TopicCard key={topic.id} topic={topic} onDecide={decide} />
            ))}
          </ul>
          <Pagination total={topics.data.total} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function TopicCard({
  topic,
  onDecide,
}: {
  topic: TopicCandidate;
  onDecide: (id: string, decision: "approved" | "rejected" | "saved") => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);

  return (
    <li className="rounded-xl border border-base-700 bg-base-900/70 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium text-base-100">{topic.title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-base-400">{topic.angle}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <OpportunityScore score={topic.opportunity_score} breakdown={topic.score_breakdown} />
          <CompetitionBadge level={topic.competition_level} />
        </div>
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        <Field label="Audience" value={topic.audience} />
        <Field label="Category" value={topic.category} />
        <Field label="Why now" value={topic.why_now} className="sm:col-span-2" />
      </dl>

      {topic.risks.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-warn-500">Risks</p>
          <ul className="mt-1 space-y-0.5">
            {topic.risks.map((risk) => (
              <li key={risk} className="text-xs text-base-400">
                · {risk}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-base-500">
        <span>
          {topic.evidence_count} source item{topic.evidence_count === 1 ? "" : "s"} across{" "}
          {topic.evidence_source_kinds.join(", ") || UNKNOWN_PLACEHOLDER}
        </span>
        <span>
          newest evidence{" "}
          {topic.newest_evidence_at
            ? formatAge((Date.now() - new Date(topic.newest_evidence_at).getTime()) / 1000)
            : UNKNOWN_PLACEHOLDER}
        </span>
        {topic.generated_by.model && (
          <span className="font-mono">
            {topic.generated_by.provider}/{topic.generated_by.model}
          </span>
        )}
        <button
          onClick={() => setShowEvidence((value) => !value)}
          className="text-accent-400 hover:underline"
        >
          {showEvidence ? "Hide evidence" : "Show evidence"}
        </button>
      </div>

      {showEvidence && (
        <ul className="mt-2 space-y-1.5 rounded-lg border border-base-800 bg-base-850/60 p-3">
          {topic.sources.map((source) => (
            <li key={source.trending_topic_id} className="text-xs">
              {source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="text-base-200 hover:text-accent-400 hover:underline"
                >
                  {source.title}
                </a>
              ) : (
                <span className="text-base-200">{source.title}</span>
              )}
              <span className="ml-2 text-base-500">
                {source.source_name} · {source.source_kind}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {topic.status === "proposed" ? (
          <>
            <Button variant="primary" onClick={() => onDecide(topic.id, "approved")}>
              Approve
            </Button>
            <Button onClick={() => onDecide(topic.id, "saved")}>Save for later</Button>
            <Button variant="ghost" onClick={() => onDecide(topic.id, "rejected")}>
              Reject
            </Button>
          </>
        ) : (
          <span className="text-xs uppercase tracking-wider text-base-400">
            {topic.status}
            {topic.decided_at && ` · ${new Date(topic.decided_at).toLocaleString()}`}
          </span>
        )}
      </div>
    </li>
  );
}

function Field({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string | null;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-[11px] uppercase tracking-[0.12em] text-base-500">{label}</dt>
      <dd className="mt-0.5 text-xs text-base-300">{value ?? UNKNOWN_PLACEHOLDER}</dd>
    </div>
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
        Page {page} of {pages} · {total} candidates
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

function errorMessage(error: ApiError | Error): string {
  if (error instanceof ApiError && error.isNotConfigured) {
    return `${error.message} Configure an LLM provider in the server environment to enable this.`;
  }
  return error.message;
}
