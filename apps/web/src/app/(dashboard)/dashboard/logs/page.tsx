"use client";

import { useState } from "react";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  StatusPill,
} from "@/components/primitives";
import type { AuditEntry, Job, Paged, ProviderStatus } from "@/lib/types";

const PAGE_SIZE = 25;

const JOB_STATUS_STYLE: Record<Job["status"], ProviderStatus> = {
  QUEUED: "NOT CONFIGURED",
  RUNNING: "DEGRADED",
  SUCCESS: "HEALTHY",
  FAILED: "UNHEALTHY",
  CANCELLED: "NOT CONNECTED",
};

export default function LogsPage() {
  const [tab, setTab] = useState<"jobs" | "audit">("jobs");
  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Logs</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Background jobs and the audit trail of every state change.
        </p>
      </header>

      <div className="flex gap-1">
        {(["jobs", "audit"] as const).map((value) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            aria-pressed={tab === value}
            className={`rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
              tab === value
                ? "bg-accent-600/15 text-accent-400"
                : "text-base-400 hover:bg-base-800 hover:text-base-200"
            }`}
          >
            {value}
          </button>
        ))}
      </div>

      {tab === "jobs" ? <JobsTable /> : <AuditTable />}
    </div>
  );
}

function JobsTable() {
  const [offset, setOffset] = useState(0);
  const jobs = useApi<Paged<Job>>(`/api/jobs?limit=${PAGE_SIZE}&offset=${offset}`);

  if (jobs.status === "loading") return <Loading label="Loading jobs" />;
  if (jobs.status === "error") return <ErrorNotice message={jobs.error.message} />;
  if (jobs.data.total === 0) {
    return (
      <EmptyState
        title="No jobs yet"
        description="Background jobs appear here as soon as the pipeline runs work — trend scans, renders, uploads and analytics syncs."
      />
    );
  }

  return (
    <Card action={<Button onClick={jobs.refresh}>Refresh</Button>}>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-base-700 text-left text-[11px] uppercase tracking-wider text-base-500">
              <th className="px-1 pb-2 font-medium">Type</th>
              <th className="px-1 pb-2 font-medium">Status</th>
              <th className="px-1 pb-2 font-medium">Attempts</th>
              <th className="px-1 pb-2 font-medium">Created</th>
              <th className="px-1 pb-2 font-medium">Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data.items.map((job) => (
              <tr key={job.id} className="border-b border-base-800 last:border-0">
                <td className="px-1 py-2 font-mono text-xs text-base-200">{job.type}</td>
                <td className="px-1 py-2">
                  <StatusPill status={JOB_STATUS_STYLE[job.status]} label={job.status} />
                </td>
                <td className="px-1 py-2 font-mono text-xs tabular-nums text-base-300">
                  {job.attempts}/{job.max_attempts}
                </td>
                <td className="px-1 py-2 text-xs text-base-400">
                  {job.created_at ? new Date(job.created_at).toLocaleString() : "—"}
                </td>
                <td className="max-w-xs px-1 py-2 text-xs text-danger-500">
                  <span className="line-clamp-2">{job.error ?? ""}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        total={jobs.data.total}
        offset={offset}
        onChange={setOffset}
      />
    </Card>
  );
}

function AuditTable() {
  const [offset, setOffset] = useState(0);
  const audit = useApi<Paged<AuditEntry>>(`/api/logs/audit?limit=${PAGE_SIZE}&offset=${offset}`);

  if (audit.status === "loading") return <Loading label="Loading audit trail" />;
  if (audit.status === "error") return <ErrorNotice message={audit.error.message} />;
  if (audit.data.total === 0) {
    return (
      <EmptyState
        title="Nothing recorded yet"
        description="Every state change — settings edits, approvals, publishes and emergency stops — is appended here."
      />
    );
  }

  return (
    <Card action={<Button onClick={audit.refresh}>Refresh</Button>}>
      <ul className="space-y-2.5">
        {audit.data.items.map((entry) => (
          <li key={entry.id} className="border-b border-base-800 pb-2.5 last:border-0 last:pb-0">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-mono text-xs text-accent-400">{entry.action}</span>
              <span className="text-[11px] text-base-500">
                {entry.created_at ? new Date(entry.created_at).toLocaleString() : "—"}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-base-400">
              {entry.summary ?? `${entry.entity_type ?? "system"} ${entry.entity_id ?? ""}`}
              <span className="ml-2 text-base-600">by {entry.actor_type}</span>
            </p>
          </li>
        ))}
      </ul>
      <Pagination total={audit.data.total} offset={offset} onChange={setOffset} />
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
    <div className="mt-4 flex items-center justify-between gap-3 text-xs text-base-400">
      <span>
        Page {page} of {pages} · {total} total
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
