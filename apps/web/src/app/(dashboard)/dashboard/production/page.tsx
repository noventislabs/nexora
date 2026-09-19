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
import { LicenseBadge, TimingBadge, formatBytes, formatDuration } from "@/components/license-badge";
import type {
  Availability,
  ContentProject,
  Paged,
  ProjectThumbnail,
  RenderListing,
  Scene,
  VoiceJob,
  VoiceStatus,
} from "@/lib/types";

export default function ProductionPage() {
  const projects = useApi<Paged<ContentProject>>("/api/content?limit=50");
  const [selected, setSelected] = useState<string | null>(null);

  if (projects.status === "loading") return <Loading label="Loading projects" />;
  if (projects.status === "error") return <ErrorNotice message={projects.error.message} />;

  const activeId = selected ?? projects.data.items[0]?.id ?? null;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Production</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Narration, rendering and thumbnails. Every runtime shown here is measured from the
          file that was produced, never estimated from the script.
        </p>
      </header>

      <CapabilityStrip />

      {projects.data.total === 0 ? (
        <EmptyState
          title="No content projects yet"
          description="Production works on a project that already has a script. Create one from a researched topic."
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
          {activeId && <ProductionPanel projectId={activeId} />}
        </>
      )}
    </div>
  );
}

function CapabilityStrip() {
  const voice = useApi<VoiceStatus>("/api/voice/status");
  const render = useApi<Availability>("/api/video/capability");

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card
        title="Voice provider"
        action={voice.status === "ready" ? <StatusPill status={voice.data.status} /> : undefined}
      >
        {voice.status === "loading" && <Loading />}
        {voice.status === "error" && <ErrorNotice message={voice.error.message} />}
        {voice.status === "ready" && (
          <>
            <p className="text-sm text-base-300">{voice.data.detail}</p>
            {voice.data.missing_settings.length > 0 && (
              <p className="mt-2 text-[11px] text-base-500">
                Set{" "}
                <code className="font-mono text-accent-400">
                  {voice.data.missing_settings.join(", ")}
                </code>{" "}
                in the server environment.
              </p>
            )}
            {voice.data.provides_timings !== undefined && (
              <p className="mt-2 text-[11px] text-base-500">
                {voice.data.provides_timings
                  ? "This provider returns real timings, so subtitles align exactly."
                  : "This provider returns no timings, so subtitle boundaries are interpolated from the measured audio duration."}
              </p>
            )}
            {voice.data.status_detail?.quota && (
              <p className="mt-2 font-mono text-[11px] text-base-400">
                quota:{" "}
                {voice.data.status_detail.quota.characters_remaining?.toLocaleString() ??
                  UNKNOWN_PLACEHOLDER}{" "}
                characters remaining
              </p>
            )}
            {voice.data.status_detail?.quota === null &&
              voice.data.status_detail.quota_note && (
                <p className="mt-2 text-[11px] text-base-500">
                  {voice.data.status_detail.quota_note}
                </p>
              )}
          </>
        )}
      </Card>

      <Card
        title="Render toolchain"
        action={render.status === "ready" ? <StatusPill status={render.data.status} /> : undefined}
      >
        {render.status === "loading" && <Loading />}
        {render.status === "error" && <ErrorNotice message={render.error.message} />}
        {render.status === "ready" && (
          <>
            <p className="text-sm text-base-300">{render.data.detail}</p>
            <p className="mt-2 font-mono text-[11px] text-base-500">
              ffmpeg {String(render.data.metadata.ffmpeg_version ?? UNKNOWN_PLACEHOLDER)}
            </p>
            {render.data.status !== "AVAILABLE" && (
              <p className="mt-2 text-xs text-warn-500">
                Without FFmpeg no video can be produced. NEXORA will not write a placeholder
                file in its place.
              </p>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

function ProductionPanel({ projectId }: { projectId: string }) {
  const voiceJobs = useApi<{ items: VoiceJob[] }>(`/api/voice/${projectId}/jobs`);
  const renders = useApi<RenderListing>(`/api/video/${projectId}/renders`);
  const thumbnails = useApi<{ items: ProjectThumbnail[]; current_thumbnail_id: string | null }>(
    `/api/thumbnails/${projectId}`,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(label: string, path: string, body: unknown, refresh: () => void) {
    setBusy(label);
    setError(null);
    try {
      await apiFetch(path, { method: "POST", body });
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `${label} failed.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      {error && <ErrorNotice message={error} />}

      <Card
        title="Narration"
        action={
          <Button
            variant="primary"
            disabled={busy !== null}
            onClick={() =>
              act(
                "Narration",
                "/api/voice/generate",
                { content_project_id: projectId, background: false },
                voiceJobs.refresh,
              )
            }
          >
            {busy === "Narration" ? "Synthesizing…" : "Generate narration"}
          </Button>
        }
      >
        {voiceJobs.status === "loading" && <Loading />}
        {voiceJobs.status === "error" && <ErrorNotice message={voiceJobs.error.message} />}
        {voiceJobs.status === "ready" &&
          (voiceJobs.data.items.length === 0 ? (
            <p className="text-sm text-base-400">
              No narration yet. A video is never rendered without real narration.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {voiceJobs.data.items.map((job) => (
                <li key={job.id} className="border-b border-base-800 pb-2.5 last:border-0">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <StatusPill
                        status={
                          job.status === "SUCCESS"
                            ? "HEALTHY"
                            : job.status === "FAILED"
                              ? "UNHEALTHY"
                              : "DEGRADED"
                        }
                        label={job.status}
                      />
                      <span className="font-mono text-xs text-base-300">
                        {job.provider ?? UNKNOWN_PLACEHOLDER}
                        {job.voice_id && ` · ${job.voice_id}`}
                      </span>
                    </span>
                    <TimingBadge source={job.timing_source} />
                  </div>
                  <p className="mt-1 text-[11px] text-base-500">
                    {formatDuration(job.duration_seconds)} ·{" "}
                    {job.character_count?.toLocaleString() ?? UNKNOWN_PLACEHOLDER} characters ·{" "}
                    {job.cue_count} cues
                  </p>
                  {job.duration_basis && (
                    <p className="mt-0.5 text-[11px] text-base-600">{job.duration_basis}</p>
                  )}
                  {job.error && <p className="mt-1 text-[11px] text-danger-500">{job.error}</p>}
                </li>
              ))}
            </ul>
          ))}
      </Card>

      <Card
        title="Render"
        action={
          <Button
            variant="primary"
            disabled={busy !== null}
            onClick={() =>
              act(
                "Render",
                "/api/video/render",
                { content_project_id: projectId, background: true },
                renders.refresh,
              )
            }
          >
            {busy === "Render" ? "Queueing…" : "Render video"}
          </Button>
        }
      >
        {renders.status === "loading" && <Loading />}
        {renders.status === "error" && <ErrorNotice message={renders.error.message} />}
        {renders.status === "ready" && (
          <>
            {renders.data.items.length === 0 ? (
              <p className="text-sm text-base-400">
                No renders yet. Rendering runs in the background and is queued to the media
                worker.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {renders.data.items.map((job) => (
                  <li key={job.id} className="border-b border-base-800 pb-2.5 last:border-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <StatusPill
                        status={
                          job.status === "SUCCESS"
                            ? "HEALTHY"
                            : job.status === "FAILED"
                              ? "UNHEALTHY"
                              : "DEGRADED"
                        }
                        label={job.status}
                      />
                      <span className="font-mono text-[11px] text-base-400">
                        {job.resolution ?? UNKNOWN_PLACEHOLDER} ·{" "}
                        {formatDuration(job.duration_seconds)} · {formatBytes(job.output_bytes)}
                      </span>
                    </div>
                    {job.duration_basis && (
                      <p className="mt-1 text-[11px] text-base-600">{job.duration_basis}</p>
                    )}
                    {job.ffmpeg_version && (
                      <p className="mt-0.5 font-mono text-[11px] text-base-600">
                        ffmpeg {job.ffmpeg_version}
                      </p>
                    )}
                    {job.error && <p className="mt-1 text-[11px] text-danger-500">{job.error}</p>}
                    {job.output_asset_id && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        <a
                          href={`/api/assets/${job.output_asset_id}/content`}
                          className="text-[11px] text-accent-400 hover:underline"
                        >
                          Download MP4
                        </a>
                        {job.subtitle_asset_id && (
                          <a
                            href={`/api/assets/${job.subtitle_asset_id}/content`}
                            className="text-[11px] text-accent-400 hover:underline"
                          >
                            Download captions (VTT)
                          </a>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {renders.data.scene_plan.length > 0 && (
              <ScenePlan scenes={renders.data.scene_plan} />
            )}
          </>
        )}
      </Card>

      <Card
        title="Thumbnails"
        action={
          <Button
            disabled={busy !== null}
            onClick={() =>
              act(
                "Thumbnail",
                "/api/thumbnails/generate",
                { content_project_id: projectId, background: false },
                thumbnails.refresh,
              )
            }
          >
            {busy === "Thumbnail" ? "Generating…" : "Generate thumbnail"}
          </Button>
        }
      >
        {thumbnails.status === "loading" && <Loading />}
        {thumbnails.status === "error" && <ErrorNotice message={thumbnails.error.message} />}
        {thumbnails.status === "ready" &&
          (thumbnails.data.items.length === 0 ? (
            <p className="text-sm text-base-400">
              No thumbnails yet. Every candidate is kept, and one must be approved before
              publishing.
            </p>
          ) : (
            <ul className="grid gap-4 sm:grid-cols-2">
              {thumbnails.data.items.map((thumbnail) => (
                <li
                  key={thumbnail.id}
                  className="overflow-hidden rounded-lg border border-base-700"
                >
                  {thumbnail.asset_id && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`/api/assets/${thumbnail.asset_id}/content`}
                      alt={thumbnail.headline ?? "Generated thumbnail"}
                      width={thumbnail.width ?? 1280}
                      height={thumbnail.height ?? 720}
                      className="w-full bg-base-850"
                      loading="lazy"
                    />
                  )}
                  <div className="space-y-2 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[11px] uppercase tracking-wider text-base-400">
                        {thumbnail.status}
                      </span>
                      {thumbnail.license_status && (
                        <LicenseBadge status={thumbnail.license_status} />
                      )}
                    </div>
                    <p className="text-xs text-base-300">
                      {thumbnail.headline ?? UNKNOWN_PLACEHOLDER}
                    </p>
                    <p className="text-[11px] text-base-500">
                      {thumbnail.width}×{thumbnail.height} · {formatBytes(thumbnail.size_bytes)}
                    </p>
                    {thumbnail.status === "generated" && (
                      <div className="flex gap-2">
                        <Button
                          variant="primary"
                          disabled={busy !== null}
                          onClick={() =>
                            act(
                              "Decision",
                              `/api/thumbnails/${projectId}/${thumbnail.id}/decision`,
                              { approved: true },
                              thumbnails.refresh,
                            )
                          }
                        >
                          Approve
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={busy !== null}
                          onClick={() =>
                            act(
                              "Decision",
                              `/api/thumbnails/${projectId}/${thumbnail.id}/decision`,
                              { approved: false },
                              thumbnails.refresh,
                            )
                          }
                        >
                          Reject
                        </Button>
                      </div>
                    )}
                    {thumbnails.data.current_thumbnail_id === thumbnail.id && (
                      <p className="text-[11px] text-accent-400">Selected for publishing</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ))}
        <p className="mt-3 text-[11px] text-base-600">
          A thumbnail is a design artefact. NEXORA makes no claim about how one will perform.
        </p>
      </Card>

      <AssetLibrary projectId={projectId} />
    </div>
  );
}

function ScenePlan({ scenes }: { scenes: Scene[] }) {
  return (
    <div className="mt-4 border-t border-base-800 pt-3">
      <p className="text-[11px] uppercase tracking-[0.12em] text-base-500">Scene plan</p>
      <ol className="mt-2 space-y-1">
        {scenes.map((scene) => (
          <li key={scene.index} className="flex items-baseline justify-between gap-3 text-[11px]">
            <span className="truncate text-base-300">
              <span className="font-mono text-base-500">{scene.kind}</span> · {scene.heading}
            </span>
            <span className="shrink-0 font-mono tabular-nums text-base-500">
              {scene.start.toFixed(1)}s → {scene.end.toFixed(1)}s
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function AssetLibrary({ projectId }: { projectId: string }) {
  const assets = useApi<import("@/lib/types").AssetListing>(
    `/api/assets?content_project_id=${projectId}&limit=50`,
  );

  if (assets.status === "loading") return <Card title="Assets"><Loading /></Card>;
  if (assets.status === "error")
    return (
      <Card title="Assets">
        <ErrorNotice message={assets.error.message} />
      </Card>
    );

  const blocked = assets.data.items.filter((asset) => asset.blocks_autonomous_publishing);

  return (
    <Card title={`Assets · ${assets.data.total}`}>
      {blocked.length > 0 && (
        <p className="mb-3 rounded-lg border border-warn-500/30 bg-warn-500/10 px-3 py-2 text-xs text-warn-500">
          {blocked.length} asset{blocked.length === 1 ? "" : "s"} have an unestablished licence.
          These block autonomous publishing until an operator records their licence.
        </p>
      )}
      {assets.data.items.length === 0 ? (
        <p className="text-sm text-base-400">No assets recorded for this project yet.</p>
      ) : (
        <ul className="space-y-2">
          {assets.data.items.map((asset) => (
            <li
              key={asset.id}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-base-800 pb-2 last:border-0 last:pb-0"
            >
              <span className="min-w-0">
                <span className="block truncate font-mono text-xs text-base-200">
                  {asset.kind} · {asset.mime_type ?? UNKNOWN_PLACEHOLDER}
                </span>
                <span className="text-[11px] text-base-500">
                  {formatBytes(asset.size_bytes)}
                  {asset.duration_seconds !== null &&
                    ` · ${formatDuration(asset.duration_seconds)}`}
                  {asset.width !== null && ` · ${asset.width}×${asset.height}`}
                  {asset.source_provider && ` · ${asset.source_provider}`}
                </span>
              </span>
              <LicenseBadge status={asset.license_status} licenseType={asset.license_type} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
