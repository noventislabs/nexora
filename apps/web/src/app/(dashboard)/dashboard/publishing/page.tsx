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
  StatusPill,
  UNKNOWN_PLACEHOLDER,
} from "@/components/primitives";
import { CopyrightPanel, PreflightPanel, QualityPanel } from "@/components/preflight";
import { formatBytes } from "@/components/license-badge";
import type {
  ContentProject,
  MetadataVersion,
  Paged,
  Preflight,
  PublishJob,
  YouTubeConnectionState,
} from "@/lib/types";

const PRIVACY_OPTIONS = ["private", "unlisted", "public"] as const;

export default function PublishingPage() {
  const projects = useApi<Paged<ContentProject>>("/api/content?limit=50");
  const connection = useApi<YouTubeConnectionState>("/api/youtube/connection");
  const [selected, setSelected] = useState<string | null>(null);

  if (projects.status === "loading" || connection.status === "loading") {
    return <Loading label="Loading publishing state" />;
  }
  if (projects.status === "error") return <ErrorNotice message={projects.error.message} />;
  if (connection.status === "error") return <ErrorNotice message={connection.error.message} />;

  const activeId = selected ?? projects.data.items[0]?.id ?? null;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Publishing</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Nothing is uploaded until every blocking gate passes and a human authorizes it.
          NEXORA cannot promise views, subscribers or revenue, and it does not try.
        </p>
      </header>

      <ConnectionSummary state={connection.data} />
      <PendingApprovals />

      {projects.data.total === 0 ? (
        <EmptyState
          title="No content projects yet"
          description="Publishing operates on a finished project: a script, a rendered video and generated metadata."
          action={
            <Link href="/dashboard/scripts">
              <Button>Go to Scripts</Button>
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
          {activeId && (
            <ProjectPublishPanel
              projectId={activeId}
              canUpload={connection.data.capabilities.upload === true}
            />
          )}
        </>
      )}

      <PublishHistory />
    </div>
  );
}

function ConnectionSummary({ state }: { state: YouTubeConnectionState }) {
  if (state.oauth_app.status === "NOT CONFIGURED") {
    return (
      <NotConfigured
        feature="YouTube upload"
        detail={state.oauth_app.detail}
        missing={state.oauth_app.missing_settings}
      />
    );
  }

  return (
    <Card
      title="YouTube channel"
      action={
        <StatusPill
          status={state.connected ? "CONNECTED" : "NOT CONNECTED"}
          label={state.connected ? "CONNECTED" : state.status.replace(/_/g, " ").toUpperCase()}
        />
      }
    >
      <p className="text-sm text-base-200">
        {state.youtube_channel_title ?? state.public_channel_title ?? UNKNOWN_PLACEHOLDER}
      </p>
      <p className="mt-1 text-xs text-base-500">{state.note}</p>
      {!state.connected && (
        <div className="mt-3">
          <Link href="/dashboard/channels">
            <Button variant="primary">Connect YouTube</Button>
          </Link>
        </div>
      )}
    </Card>
  );
}

function PendingApprovals() {
  const pending = useApi<{ items: ContentProject[]; total: number }>(
    "/api/publish/pending/approvals",
  );
  if (pending.status !== "ready" || pending.data.total === 0) return null;

  return (
    <Card title={`Awaiting your approval (${pending.data.total})`}>
      <ul className="space-y-2">
        {pending.data.items.map((project) => (
          <li
            key={project.id}
            className="flex items-center justify-between gap-3 rounded-lg border border-base-800 px-3 py-2"
          >
            <span className="truncate text-sm text-base-200">{project.title}</span>
            <span className="shrink-0 text-xs text-base-500">{project.status}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ProjectPublishPanel({
  projectId,
  canUpload,
}: {
  projectId: string;
  canUpload: boolean;
}) {
  const preflight = useApi<Preflight>(`/api/publish/preflight/${projectId}`, [projectId]);
  const metadata = useApi<MetadataVersion>(`/api/metadata/${projectId}`, [projectId]);
  const [privacy, setPrivacy] = useState<(typeof PRIVACY_OPTIONS)[number]>("private");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function act(label: string, run: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await run();
      preflight.refresh();
      metadata.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The request failed.");
    } finally {
      setBusy(null);
    }
  }

  if (preflight.status === "loading") return <Loading label="Running preflight" />;
  if (preflight.status === "error") return <ErrorNotice message={preflight.error.message} />;

  const ready = preflight.data.can_publish && canUpload;

  return (
    <div className="space-y-4">
      {error && <ErrorNotice message={error} />}
      {notice && (
        <div className="rounded-lg border border-ok-500/30 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {notice}
        </div>
      )}

      <PreflightPanel preflight={preflight.data} />

      <div className="grid gap-4 lg:grid-cols-2">
        {preflight.data.quality && <QualityPanel quality={preflight.data.quality} />}
        {preflight.data.copyright && <CopyrightPanel copyright={preflight.data.copyright} />}
      </div>

      <Card
        title="Metadata"
        action={
          <Button
            onClick={() =>
              act("metadata", async () => {
                await apiFetch("/api/metadata/generate", {
                  method: "POST",
                  body: { content_project_id: projectId, background: false },
                });
                setNotice("Metadata generated.");
              })
            }
            disabled={busy !== null}
          >
            {busy === "metadata" ? "Generating…" : "Generate metadata"}
          </Button>
        }
      >
        {metadata.status === "error" ? (
          <p className="text-sm text-base-400">
            No metadata has been generated for this project yet.
          </p>
        ) : metadata.status === "loading" ? (
          <Loading label="Loading metadata" />
        ) : (
          <MetadataView metadata={metadata.data} />
        )}
      </Card>

      <Card title="Approval and upload">
        <p className="text-sm text-base-300">
          Approval is a human decision recorded against your account. Autopilot can never
          record it for you, and it can never override a blocked preflight.
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={busy !== null}
            onClick={() =>
              act("approve", async () => {
                await apiFetch(`/api/publish/approve/${projectId}`, {
                  method: "POST",
                  body: { approved: true },
                });
                setNotice("Approval recorded.");
              })
            }
          >
            {busy === "approve" ? "Recording…" : "Approve"}
          </Button>
          <Button
            variant="danger"
            disabled={busy !== null}
            onClick={() =>
              act("reject", async () => {
                await apiFetch(`/api/publish/approve/${projectId}`, {
                  method: "POST",
                  body: { approved: false },
                });
                setNotice("Rejection recorded.");
              })
            }
          >
            Reject
          </Button>
        </div>

        <div className="mt-5 border-t border-base-800 pt-4">
          <label className="block">
            <span className="mb-1.5 block text-xs text-base-400">Privacy on YouTube</span>
            <select
              value={privacy}
              onChange={(event) =>
                setPrivacy(event.target.value as (typeof PRIVACY_OPTIONS)[number])
              }
              className="w-full max-w-xs rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
            >
              {PRIVACY_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-2 text-xs text-base-500">
            Private is the default. Upload it privately, watch it yourself, then change the
            visibility on YouTube when you are satisfied.
          </p>

          <div className="mt-4">
            <Button
              variant="primary"
              disabled={!ready || busy !== null}
              onClick={() =>
                act("publish", async () => {
                  const result = await apiFetch<{ publish_job: PublishJob }>("/api/publish", {
                    method: "POST",
                    body: {
                      content_project_id: projectId,
                      privacy_status: privacy,
                      background: true,
                    },
                  });
                  setNotice(
                    `Upload queued (job ${result.publish_job.id.slice(0, 8)}). The video appears below once YouTube confirms it.`,
                  );
                })
              }
            >
              {busy === "publish" ? "Queueing…" : "Publish to YouTube"}
            </Button>
            {!ready && (
              <p className="mt-2 text-xs text-base-500">
                {canUpload
                  ? "Publishing is blocked until every blocker above is cleared."
                  : "This channel has no OAuth connection, so nothing can be uploaded to it."}
              </p>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}

function MetadataView({ metadata }: { metadata: MetadataVersion }) {
  return (
    <div className="space-y-3">
      <Field
        label={`Title (${metadata.title_length}/${metadata.limits.title})`}
        value={metadata.title}
      />
      <Field
        label={`Description (${metadata.description_length}/${metadata.limits.description})`}
        value={metadata.description}
        multiline
      />
      <div>
        <p className="mb-1.5 text-xs text-base-400">
          Tags ({metadata.tags_total_chars}/{metadata.limits.tags_total_chars} characters)
        </p>
        <div className="flex flex-wrap gap-1.5">
          {metadata.tags.map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-base-700 bg-base-850 px-2.5 py-0.5 text-xs text-base-300"
            >
              {tag}
            </span>
          ))}
        </div>
      </div>
      <p className="text-xs text-base-500">
        Made for kids:{" "}
        <span className="text-base-300">
          {metadata.made_for_kids === null
            ? "NOT DECLARED"
            : metadata.made_for_kids
              ? "Yes"
              : "No"}
        </span>
        {" · "}
        Generated by {metadata.provider ?? UNKNOWN_PLACEHOLDER}
        {metadata.model ? ` (${metadata.model})` : ""}
      </p>
    </div>
  );
}

function Field({
  label,
  value,
  multiline = false,
}: {
  label: string;
  value: string;
  multiline?: boolean;
}) {
  return (
    <div>
      <p className="mb-1 text-xs text-base-400">{label}</p>
      <p
        className={`rounded-lg border border-base-800 bg-base-850/60 px-3 py-2 text-sm text-base-200 ${
          multiline ? "whitespace-pre-wrap" : "truncate"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function PublishHistory() {
  const jobs = useApi<Paged<PublishJob>>("/api/publish?limit=10");
  if (jobs.status !== "ready") return null;

  return (
    <Card title="Upload history" action={<Button onClick={jobs.refresh}>Refresh</Button>}>
      {jobs.data.total === 0 ? (
        <p className="text-sm text-base-400">
          Nothing has been uploaded from this channel yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {jobs.data.items.map((job) => (
            <li
              key={job.id}
              data-testid="publish-job"
              className="rounded-lg border border-base-800 px-3 py-2.5"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-base-400">{job.id.slice(0, 8)}</span>
                <span className="text-xs text-base-300">
                  {job.status} · {job.privacy_status} · attempt {job.attempt_count}/
                  {job.max_attempts}
                </span>
              </div>
              {job.youtube_url ? (
                <a
                  href={job.youtube_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="mt-1 inline-block text-xs text-accent-400 hover:underline"
                >
                  {job.youtube_url}
                </a>
              ) : (
                <p className="mt-1 text-xs text-base-500">
                  No YouTube video id yet — nothing has been confirmed by YouTube.
                </p>
              )}
              {job.last_error && (
                <p className="mt-1 text-xs text-danger-500">{job.last_error}</p>
              )}
              <p className="mt-1 text-[11px] text-base-500">
                Authorized by {job.authorized_by}
                {job.upload_bytes ? ` · ${formatBytes(job.upload_bytes)} uploaded` : ""}
                {job.verified_at ? " · verified on YouTube" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
