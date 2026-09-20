"use client";

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  StatusPill,
} from "@/components/primitives";
import type { Capabilities, Channel, CurrentUser, Paged } from "@/lib/types";

type ChannelSettings = {
  default_video_format: "long_form" | "short";
  target_duration_min_seconds: number;
  target_duration_max_seconds: number;
  narration_tone: string;
  call_to_action: string | null;
  aspect_ratio: "16:9" | "9:16";
  resolution: "720p" | "1080p";
  subtitle_burn_in: boolean;
  preferred_voice_id: string | null;
  editorial_notes: string | null;
  /** Tri-state: `null` means undecided, and undecided blocks publishing. */
  made_for_kids_default: boolean | null;
  youtube_category_id: string | null;
};

/**
 * YouTube category ids, as the Data API defines them. The list is short on purpose:
 * these are the categories NEXORA's own content vocabulary maps onto.
 */
const YOUTUBE_CATEGORIES: [string, string][] = [
  ["28", "Science & Technology"],
  ["27", "Education"],
  ["25", "News & Politics"],
  ["22", "People & Blogs"],
  ["24", "Entertainment"],
  ["26", "Howto & Style"],
  ["20", "Gaming"],
  ["1", "Film & Animation"],
];

export default function SettingsPage() {
  const user = useApi<CurrentUser>("/api/auth/me");
  const capabilities = useApi<Capabilities>("/api/system/capabilities");
  const channels = useApi<Paged<Channel>>("/api/channels");

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">Settings</h1>
        <p className="mt-0.5 text-sm text-base-400">
          Account, editorial defaults and the integration status of this deployment.
        </p>
      </header>

      <Card title="Account">
        {user.status === "loading" && <Loading />}
        {user.status === "error" && <ErrorNotice message={user.error.message} />}
        {user.status === "ready" && (
          <div className="space-y-1.5 text-sm">
            <Row label="Name" value={user.data.display_name} />
            <Row label="Email" value={user.data.email} />
            <Row label="Role" value={user.data.role} />
          </div>
        )}
      </Card>

      <Card title="Integrations">
        <p className="mb-3 text-sm text-base-400">
          Credentials live only in the server environment. Nothing here is ever sent to the
          browser — this panel shows status, never values.
        </p>
        {capabilities.status === "loading" && <Loading />}
        {capabilities.status === "error" && <ErrorNotice message={capabilities.error.message} />}
        {capabilities.status === "ready" && (
          <ul className="space-y-2.5">
            {Object.entries(capabilities.data).map(([key, availability]) => (
              <li key={key} className="border-b border-base-800 pb-2.5 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs uppercase tracking-wide text-base-300">
                    {key.replace(/_/g, " ")}
                  </span>
                  <StatusPill status={availability.status} />
                </div>
                <p className="mt-1 text-xs text-base-400">{availability.detail}</p>
                {availability.missing_settings.length > 0 && (
                  <p className="mt-1 text-[11px] text-base-500">
                    Set{" "}
                    <code className="font-mono text-accent-400">
                      {availability.missing_settings.join(", ")}
                    </code>{" "}
                    in the server environment.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {channels.status === "ready" && channels.data.items.length === 0 && (
        <EmptyState
          title="No editorial defaults yet"
          description="Create a channel to configure video format, duration and narration defaults."
        />
      )}
      {channels.status === "ready" &&
        channels.data.items.map((channel) => (
          <ChannelSettingsCard key={channel.id} channel={channel} />
        ))}
    </div>
  );
}

function ChannelSettingsCard({ channel }: { channel: Channel }) {
  const settings = useApi<ChannelSettings>(`/api/channels/${channel.id}/settings`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function patch(body: Partial<ChannelSettings>) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/channels/${channel.id}/settings`, { method: "PATCH", body });
      settings.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save settings.");
    } finally {
      setBusy(false);
    }
  }

  if (settings.status === "loading") return <Card title={channel.name}><Loading /></Card>;
  if (settings.status === "error")
    return (
      <Card title={channel.name}>
        <ErrorNotice message={settings.error.message} />
      </Card>
    );

  const data = settings.data;

  return (
    <Card title={`${channel.name} · editorial defaults`}>
      {error && <ErrorNotice message={error} />}
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <Select
          label="Video format"
          value={data.default_video_format}
          options={[
            ["long_form", "Long-form"],
            ["short", "Short"],
          ]}
          disabled={busy}
          onChange={(value) => patch({ default_video_format: value as ChannelSettings["default_video_format"] })}
        />
        <Select
          label="Aspect ratio"
          value={data.aspect_ratio}
          options={[
            ["16:9", "16:9 (landscape)"],
            ["9:16", "9:16 (vertical)"],
          ]}
          disabled={busy}
          onChange={(value) => patch({ aspect_ratio: value as ChannelSettings["aspect_ratio"] })}
        />
        <Select
          label="Resolution"
          value={data.resolution}
          options={[
            ["1080p", "1080p"],
            ["720p", "720p"],
          ]}
          disabled={busy}
          onChange={(value) => patch({ resolution: value as ChannelSettings["resolution"] })}
        />
        <Select
          label="Narration tone"
          value={data.narration_tone}
          options={[
            ["informative", "Informative"],
            ["analytical", "Analytical"],
            ["conversational", "Conversational"],
          ]}
          disabled={busy}
          onChange={(value) => patch({ narration_tone: value })}
        />
      </div>
      <p className="mt-3 text-xs text-base-400">
        Target duration: {Math.round(data.target_duration_min_seconds / 60)}–
        {Math.round(data.target_duration_max_seconds / 60)} minutes.
      </p>
      <div className="mt-3">
        <Button
          disabled={busy}
          onClick={() => patch({ subtitle_burn_in: !data.subtitle_burn_in })}
        >
          Burn-in subtitles: {data.subtitle_burn_in ? "ON" : "OFF"}
        </Button>
      </div>

      <div className="mt-5 border-t border-base-800 pt-4">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-base-400">
          YouTube upload defaults
        </h3>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Select
            label="Category"
            value={data.youtube_category_id ?? ""}
            options={[["", "Not set"], ...YOUTUBE_CATEGORIES]}
            disabled={busy}
            onChange={(value) => patch({ youtube_category_id: value || null })}
          />
          <Select
            label="Made for kids"
            value={
              data.made_for_kids_default === null
                ? "undecided"
                : data.made_for_kids_default
                  ? "yes"
                  : "no"
            }
            options={[
              ["undecided", "NOT DECLARED — blocks publishing"],
              ["no", "No — not made for kids"],
              ["yes", "Yes — made for kids"],
            ]}
            disabled={busy}
            onChange={(value) => {
              if (value === "undecided") return;
              patch({ made_for_kids_default: value === "yes" });
            }}
          />
        </div>

        <p
          data-testid="made-for-kids-note"
          className={`mt-3 text-xs ${
            data.made_for_kids_default === null ? "text-warn-500" : "text-base-500"
          }`}
        >
          {data.made_for_kids_default === null
            ? "This channel has not declared whether its videos are made for children. YouTube requires the declaration on every upload and it carries legal weight, so NEXORA blocks publishing until you set it. NEXORA will not guess."
            : "Every upload from this channel will carry this declaration. Change it here if the channel's audience changes."}
        </p>
      </div>
    </Card>
  );
}

function Select({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs text-base-400">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-base-800 py-1.5 last:border-0">
      <span className="text-base-400">{label}</span>
      <span className="truncate text-right text-base-200">{value}</span>
    </div>
  );
}
