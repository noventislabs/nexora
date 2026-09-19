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
} from "@/components/primitives";
import { ClassificationBadge, ClassificationLegend, FetchDecisionTag } from "@/components/classification";
import type { Capabilities, Paged, Research, TopicCandidate } from "@/lib/types";

const PAGE_SIZE = 10;

export default function ResearchPage() {
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const research = useApi<Paged<Research>>(`/api/research?limit=${PAGE_SIZE}&offset=${offset}`);
  const approved = useApi<Paged<TopicCandidate>>("/api/topics?status=approved&limit=25");
  const capabilities = useApi<Capabilities>("/api/system/capabilities");

  const llm = capabilities.status === "ready" ? capabilities.data.llm : null;
  const llmReady = llm ? llm.status === "AVAILABLE" || llm.status === "HEALTHY" : false;

  async function runResearch(candidateId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/research", {
        method: "POST",
        body: { topic_candidate_id: candidateId, background: false },
      });
      research.refresh();
      approved.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Research could not be run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Research</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Evidence gathered before any script is written. Every statement is tied to a source
          document, and statements that cannot be tied to one are discarded rather than kept.
        </p>
      </header>

      {llm && !llmReady && (
        <NotConfigured
          feature="Research"
          detail={`${llm.detail} Research is never produced without a provider — nothing here is generated locally.`}
          missing={llm.missing_settings}
        />
      )}
      {error && <ErrorNotice message={error} />}

      {llmReady && approved.status === "ready" && approved.data.items.length > 0 && (
        <Card title="Approved topics awaiting research">
          <ul className="space-y-2">
            {approved.data.items.map((topic) => (
              <li
                key={topic.id}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-base-800 pb-2 last:border-0 last:pb-0"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm text-base-200">{topic.title}</span>
                  <span className="text-[11px] text-base-500">
                    {topic.evidence_count} source item(s)
                  </span>
                </span>
                <Button variant="primary" disabled={busy} onClick={() => runResearch(topic.id)}>
                  Research
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {research.status === "loading" && <Loading label="Loading research" />}
      {research.status === "error" && <ErrorNotice message={research.error.message} />}
      {research.status === "ready" && research.data.total === 0 && (
        <EmptyState
          title="No research yet"
          description="Approve a topic under Ideas, then research it. Research is what a script is written from."
          action={
            <Link href="/dashboard/ideas">
              <Button>Go to Ideas</Button>
            </Link>
          }
        />
      )}
      {research.status === "ready" &&
        research.data.items.map((item) => <ResearchCard key={item.id} research={item} />)}

      <Card title="What the classifications mean">
        <ClassificationLegend />
      </Card>
    </div>
  );
}

function ResearchCard({ research }: { research: Research }) {
  const [open, setOpen] = useState(false);
  const detail = useApi<Research>(open ? `/api/research/${research.id}` : null);
  const shown = detail.status === "ready" ? detail.data : research;

  return (
    <Card
      title={`Research · ${shown.status}`}
      action={
        <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
          {open ? "Collapse" : "Expand"}
        </Button>
      }
    >
      <p className="text-sm text-base-200">{shown.summary ?? "No summary was produced."}</p>
      <p className="mt-2 text-[11px] text-base-500">
        {shown.document_count} document(s) · {shown.key_facts.length} fact(s) ·{" "}
        {shown.claims.length} claim(s) · {shown.conflicts.length} conflict(s)
        {shown.model && ` · ${shown.provider}/${shown.model}`}
      </p>
      {shown.error && <ErrorNotice message={shown.error} />}

      {open && (
        <div className="mt-4 space-y-4">
          {shown.conflicts.length > 0 && (
            <section>
              <h3 className="text-[11px] uppercase tracking-[0.12em] text-warn-500">
                Sources disagree
              </h3>
              <p className="mt-1 text-[11px] text-base-500">
                These are preserved deliberately. The script must present both positions rather
                than pick one.
              </p>
              <ul className="mt-2 space-y-2">
                {shown.conflicts.map((conflict) => (
                  <li
                    key={conflict.subject}
                    className="rounded-lg border border-warn-500/25 bg-warn-500/5 p-3"
                  >
                    <p className="text-xs font-medium text-base-100">{conflict.subject}</p>
                    <ul className="mt-1.5 space-y-1">
                      {conflict.positions.map((position) => (
                        <li key={position.position} className="text-xs text-base-300">
                          · {position.position}
                          <span className="ml-1.5 font-mono text-[10px] text-base-500">
                            [{position.document_indices.join(", ")}]
                          </span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <StatementList title="Key facts" items={shown.key_facts} />
          <StatementList title="Claims" items={shown.claims} />

          {shown.statistics.length > 0 && (
            <section>
              <h3 className="text-[11px] uppercase tracking-[0.12em] text-base-400">Statistics</h3>
              <ul className="mt-2 space-y-1.5">
                {shown.statistics.map((statistic) => (
                  <li key={`${statistic.value}-${statistic.what_it_measures}`} className="text-xs">
                    <span className="font-mono text-base-100">{statistic.value}</span>
                    <span className="text-base-400"> — {statistic.what_it_measures}</span>
                    <span className="text-base-500">
                      {" "}
                      (as of {statistic.as_of ?? UNKNOWN_PLACEHOLDER})
                    </span>
                    <span className="ml-1.5 font-mono text-[10px] text-base-500">
                      [{statistic.document_indices.join(", ")}]
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {shown.uncertainties.length > 0 && (
            <section>
              <h3 className="text-[11px] uppercase tracking-[0.12em] text-base-400">
                What the sources do not settle
              </h3>
              <ul className="mt-1.5 space-y-1">
                {shown.uncertainties.map((item) => (
                  <li key={item} className="text-xs text-base-400">
                    · {item}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {shown.documents && (
            <section>
              <h3 className="text-[11px] uppercase tracking-[0.12em] text-base-400">
                Source documents
              </h3>
              <ul className="mt-2 space-y-2">
                {shown.documents.map((document) => (
                  <li key={document.id} className="border-b border-base-800 pb-2 last:border-0">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-mono text-[10px] text-base-500">
                        [{document.index}]
                      </span>
                      <FetchDecisionTag decision={document.fetch_decision} />
                    </div>
                    <p className="text-xs text-base-200">
                      {document.url ? (
                        <a
                          href={document.url}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                          className="hover:text-accent-400 hover:underline"
                        >
                          {document.title}
                        </a>
                      ) : (
                        document.title
                      )}
                    </p>
                    <p className="mt-0.5 text-[11px] text-base-500">
                      {document.publisher ?? UNKNOWN_PLACEHOLDER}
                      {document.word_count !== null && ` · ${document.word_count} words`}
                      {document.truncated && " · truncated"}
                    </p>
                    {document.fetch_note && (
                      <p className="mt-0.5 text-[11px] text-base-600">{document.fetch_note}</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </Card>
  );
}

function StatementList({
  title,
  items,
}: {
  title: string;
  items: Research["key_facts"];
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-[11px] uppercase tracking-[0.12em] text-base-400">{title}</h3>
      <ul className="mt-2 space-y-2">
        {items.map((item) => (
          <li key={item.statement} className="flex items-start gap-2">
            <ClassificationBadge value={item.classification} />
            <span className="text-xs leading-relaxed text-base-300">
              {item.statement}
              {item.attributed_to && (
                <span className="text-base-500"> — per {item.attributed_to}</span>
              )}
              <span className="ml-1.5 font-mono text-[10px] text-base-500">
                [{item.document_indices.join(", ")}]
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
