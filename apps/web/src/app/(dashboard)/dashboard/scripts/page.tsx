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
  StatusPill,
  UNKNOWN_PLACEHOLDER,
} from "@/components/primitives";
import type {
  ContentProject,
  FactCheck,
  Originality,
  Paged,
  ProjectDetail,
  ScriptVersion,
} from "@/lib/types";

export default function ScriptsPage() {
  const projects = useApi<Paged<ContentProject> & { counts: Record<string, number> }>(
    "/api/content?limit=50",
  );
  const [selected, setSelected] = useState<string | null>(null);

  if (projects.status === "loading") return <Loading label="Loading content projects" />;
  if (projects.status === "error") return <ErrorNotice message={projects.error.message} />;

  const activeId = selected ?? projects.data.items[0]?.id ?? null;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Scripts</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Every generation is stored as a new version. Nothing is overwritten, so any earlier
          draft can be reselected.
        </p>
      </header>

      {projects.data.total === 0 ? (
        <EmptyState
          title="No content projects yet"
          description="A project is created from an approved topic that has been researched. A script is only ever written against collected evidence."
          action={
            <Link href="/dashboard/research">
              <Button>Go to Research</Button>
            </Link>
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {projects.data.items.map((project) => (
              <button
                key={project.id}
                onClick={() => setSelected(project.id)}
                aria-pressed={activeId === project.id}
                className={`max-w-xs truncate rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  activeId === project.id
                    ? "border-accent-600 bg-accent-600/15 text-accent-400"
                    : "border-base-700 text-base-400 hover:text-base-200"
                }`}
              >
                {project.title}
              </button>
            ))}
          </div>
          {activeId && <ProjectPanel projectId={activeId} />}
        </>
      )}
    </div>
  );
}

function ProjectPanel({ projectId }: { projectId: string }) {
  const detail = useApi<ProjectDetail>(`/api/content/${projectId}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [originality, setOriginality] = useState<Originality | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<{ originality: Originality }>("/api/scripts/generate", {
        method: "POST",
        body: { content_project_id: projectId, background: false },
      });
      setOriginality(result.originality);
      detail.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Script generation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function factCheck() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/fact-check", {
        method: "POST",
        body: { content_project_id: projectId, background: false },
      });
      detail.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The fact check could not be run.");
    } finally {
      setBusy(false);
    }
  }

  async function selectVersion(versionId: string) {
    setBusy(true);
    try {
      await apiFetch(`/api/scripts/${projectId}/select-version`, {
        method: "POST",
        body: { script_version_id: versionId },
      });
      detail.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not select that version.");
    } finally {
      setBusy(false);
    }
  }

  if (detail.status === "loading") return <Loading label="Loading project" />;
  if (detail.status === "error") return <ErrorNotice message={detail.error.message} />;

  const project = detail.data;
  const current = project.script.current;

  return (
    <div className="space-y-5">
      {error && <ErrorNotice message={error} />}

      <Card title={project.title}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-base-500">
          <span className="uppercase tracking-wider text-base-300">{project.status}</span>
          <span>{project.video_format.replace("_", " ")}</span>
          <span>target {Math.round(project.target_duration_seconds / 60)} min</span>
          <span>{project.language}</span>
          <span>approval: {project.approval_status}</span>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy} onClick={generate}>
            {project.script.current_version === 0 ? "Generate script" : "Regenerate"}
          </Button>
          <Button disabled={busy || !current} onClick={factCheck}>
            Run fact check
          </Button>
        </div>
      </Card>

      {originality && <OriginalityCard originality={originality} />}
      {project.fact_check && <FactCheckCard check={project.fact_check} />}

      {project.script.versions.length > 0 && (
        <Card title="Versions">
          <ul className="space-y-2">
            {project.script.versions.map((version) => (
              <li
                key={version.id}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-base-800 pb-2 last:border-0 last:pb-0"
              >
                <span>
                  <span className="font-mono text-sm text-base-100">v{version.version}</span>
                  <span className="ml-2 text-[11px] text-base-500">
                    {version.word_count} words · ~
                    {Math.round(version.estimated_duration_seconds / 60)} min ·{" "}
                    {version.section_count} sections
                    {version.model && ` · ${version.model}`}
                  </span>
                </span>
                {project.current_script_version_id === version.id ? (
                  <span className="text-[11px] uppercase tracking-wider text-accent-400">
                    current
                  </span>
                ) : (
                  <Button variant="ghost" disabled={busy} onClick={() => selectVersion(version.id)}>
                    Use this version
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {current ? <ScriptView version={current} /> : (
        <EmptyState
          title="No script yet"
          description="Generate a script from this project's research. Each generation is stored as a new version."
        />
      )}
    </div>
  );
}

function ScriptView({ version }: { version: ScriptVersion }) {
  return (
    <Card title={`Script v${version.version}`}>
      <p className="mb-3 text-[11px] text-base-500">{version.duration_basis}</p>
      {version.notes && (
        <p className="mb-4 rounded-lg border border-base-700 bg-base-850/60 p-3 text-xs text-base-300">
          {version.notes}
        </p>
      )}
      <ol className="space-y-4">
        {(version.sections ?? []).map((section, index) => (
          <li key={`${section.kind}-${index}`}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-xs font-medium text-base-100">{section.heading}</h3>
              <span className="font-mono text-[10px] uppercase tracking-wider text-base-500">
                {section.kind.replace(/_/g, " ")}
                {section.document_indices.length > 0 &&
                  ` · sources [${section.document_indices.join(", ")}]`}
              </span>
            </div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-base-300">
              {section.narration}
            </p>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function OriginalityCard({ originality }: { originality: Originality }) {
  const tone = !originality.conclusive
    ? "NOT CONFIGURED"
    : originality.score >= 90
      ? "HEALTHY"
      : originality.score >= 70
        ? "DEGRADED"
        : "UNHEALTHY";
  return (
    <Card
      title="Originality"
      action={
        <StatusPill
          status={tone}
          label={originality.conclusive ? `${originality.score}/100` : "INCONCLUSIVE"}
        />
      }
    >
      <p className="text-sm text-base-300">{originality.scope}</p>
      {!originality.conclusive && (
        <p className="mt-2 text-xs text-warn-500">
          No meaningful comparison was possible, so this is not evidence of originality.
        </p>
      )}
      {originality.conclusive && (
        <p className="mt-2 text-[11px] text-base-500">
          Longest verbatim run: {originality.longest_verbatim_run_words} words ·{" "}
          {originality.checked_documents} document(s) compared.
        </p>
      )}
      {originality.matches.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {originality.matches.map((match) => (
            <li key={match.excerpt} className="text-[11px] text-danger-500">
              {match.words} words matching document [{match.document_index}]:{" "}
              <span className="text-base-400">&ldquo;{match.excerpt}&rdquo;</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function FactCheckCard({ check }: { check: FactCheck }) {
  const tone =
    check.status === "PASS" ? "HEALTHY" : check.status === "REVIEW" ? "DEGRADED" : "UNHEALTHY";
  return (
    <Card title="Fact check" action={<StatusPill status={tone} label={check.status} />}>
      <div className="grid grid-cols-3 gap-2.5">
        <Tally label="Supported" value={check.supported} tone="text-ok-500" />
        <Tally label="Needs review" value={check.needs_review} tone="text-warn-500" />
        <Tally label="Unsupported" value={check.unsupported} tone="text-danger-500" />
      </div>

      {check.blocks_publishing && (
        <p className="mt-3 rounded-lg border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-xs text-danger-500">
          This result blocks automatic publishing. Fix the unsupported assertions and regenerate,
          or publish manually after reviewing them.
        </p>
      )}

      {check.claims.length > 0 && (
        <ul className="mt-4 space-y-2.5">
          {check.claims.map((claim) => (
            <li key={claim.assertion} className="border-b border-base-800 pb-2.5 last:border-0">
              <div className="flex items-start gap-2">
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-wider ${
                    claim.verdict === "SUPPORTED"
                      ? "border-ok-500/30 bg-ok-500/10 text-ok-500"
                      : claim.verdict === "NEEDS_REVIEW"
                        ? "border-warn-500/30 bg-warn-500/10 text-warn-500"
                        : "border-danger-500/30 bg-danger-500/10 text-danger-500"
                  }`}
                >
                  {claim.verdict.replace("_", " ")}
                </span>
                <span className="text-xs leading-relaxed text-base-300">
                  {claim.assertion}
                  {claim.document_indices.length > 0 && (
                    <span className="ml-1.5 font-mono text-[10px] text-base-500">
                      [{claim.document_indices.join(", ")}]
                    </span>
                  )}
                </span>
              </div>
              {claim.reasons.length > 0 && (
                <p className="mt-1 pl-1 text-[11px] text-base-500">{claim.reasons.join(" ")}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-[11px] text-base-600">
        Checked against this project&rsquo;s research documents only
        {check.model && ` · ${check.provider}/${check.model}`}
        {check.created_at && ` · ${new Date(check.created_at).toLocaleString()}`}
      </p>
    </Card>
  );
}

function Tally({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-lg border border-base-800 bg-base-850/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.12em] text-base-500">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}
