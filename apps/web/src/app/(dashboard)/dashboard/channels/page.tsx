"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  StatusPill,
} from "@/components/primitives";
import { YouTubeConnectionCard } from "@/components/youtube-connection";
import { ChannelProfileEditor } from "@/components/channel-profile";
import type { Channel, ContentCategory, Paged } from "@/lib/types";

export default function ChannelsPage() {
  return (
    <Suspense fallback={<Loading label="Loading channels" />}>
      <ChannelsView />
    </Suspense>
  );
}

/**
 * Google sends the browser back here after the consent screen, carrying either
 * ``?youtube_connected=<channel id>`` or ``?youtube_error=<code>``. The banner reports
 * whichever actually arrived — a failed consent is never rendered as a success.
 */
function OAuthResultBanner() {
  const params = useSearchParams();
  const connected = params.get("youtube_connected");
  const error = params.get("youtube_error");

  if (error) {
    return (
      <div
        role="alert"
        data-testid="oauth-error"
        className="rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-500"
      >
        YouTube did not complete the connection:{" "}
        <code className="font-mono">{error}</code>. Nothing was connected, and NEXORA
        cannot upload to this channel.
      </div>
    );
  }
  if (connected) {
    return (
      <div
        data-testid="oauth-connected"
        className="rounded-lg border border-ok-500/30 bg-ok-500/10 px-4 py-3 text-sm text-ok-500"
      >
        Connected to YouTube channel <code className="font-mono">{connected}</code>.
      </div>
    );
  }
  return null;
}

function ChannelsView() {
  const channels = useApi<Paged<Channel>>("/api/channels");
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<{ id: string; panel: "youtube" | "profile" } | null>(
    null,
  );

  const isOpen = (id: string, panel: "youtube" | "profile") =>
    expanded?.id === id && expanded.panel === panel;
  const toggle = (id: string, panel: "youtube" | "profile") =>
    setExpanded((current) =>
      current?.id === id && current.panel === panel ? null : { id, panel },
    );

  if (channels.status === "loading") return <Loading label="Loading channels" />;
  if (channels.status === "error") return <ErrorNotice message={channels.error.message} />;

  return (
    <div className="space-y-5">
      <OAuthResultBanner />
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-base-100">Channels</h1>
          <p className="mt-0.5 text-sm text-base-400">
            Each channel has its own settings, automation limits and YouTube connection.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating((value) => !value)}>
          {creating ? "Cancel" : "New channel"}
        </Button>
      </header>

      {creating && (
        <CreateChannelForm
          onCreated={() => {
            setCreating(false);
            channels.refresh();
          }}
        />
      )}

      {channels.data.items.length === 0 && !creating ? (
        <EmptyState
          title="No channels yet"
          description="Create your first channel. It starts with autopilot OFF, auto-publishing OFF and human approval required."
          action={
            <Button variant="primary" onClick={() => setCreating(true)}>
              Create channel
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {channels.data.items.map((channel) => (
            <Card key={channel.id} title={channel.name}>
              <dl className="space-y-1.5 text-sm">
                <Row label="Timezone" value={channel.timezone} />
                <Row
                  label="Languages"
                  value={`${channel.primary_language}${channel.secondary_language ? ` → ${channel.secondary_language}` : ""}`}
                />
                <Row label="Categories" value={channel.categories.join(", ")} />
              </dl>
              <div className="mt-4 border-t border-base-800 pt-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs uppercase tracking-[0.12em] text-base-500">YouTube</span>
                  <StatusPill status={channel.youtube?.status ?? "NOT CONNECTED"} />
                </div>
                <p className="mt-2 text-xs text-base-400">
                  {channel.youtube?.detail ?? "No YouTube channel is connected."}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    onClick={() => toggle(channel.id, "youtube")}
                    aria-expanded={isOpen(channel.id, "youtube")}
                  >
                    {isOpen(channel.id, "youtube") ? "Hide connection" : "Manage YouTube"}
                  </Button>
                  <Button
                    onClick={() => toggle(channel.id, "profile")}
                    aria-expanded={isOpen(channel.id, "profile")}
                  >
                    {isOpen(channel.id, "profile") ? "Hide profile" : "Channel profile"}
                  </Button>
                </div>
                {isOpen(channel.id, "youtube") && (
                  <div className="mt-4">
                    <YouTubeConnectionCard
                      channelId={channel.id}
                      onChanged={channels.refresh}
                    />
                  </div>
                )}
                {isOpen(channel.id, "profile") && (
                  <div className="mt-4">
                    <ChannelProfileEditor channelId={channel.id} />
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-base-800 py-1.5 last:border-0">
      <dt className="text-base-400">{label}</dt>
      <dd className="truncate text-right text-base-200">{value}</dd>
    </div>
  );
}

function CreateChannelForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("Asia/Dhaka");
  const [primary, setPrimary] = useState("en");
  const [secondary, setSecondary] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Read from the API: the vocabulary is stored data a channel can extend, so any
  // list hard-coded here would silently narrow what NEXORA supports.
  const catalog = useApi<{ items: ContentCategory[] }>("/api/categories");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/api/channels", {
        method: "POST",
        body: {
          name,
          timezone,
          primary_language: primary,
          secondary_language: secondary || null,
          categories: selected,
        },
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the channel.");
      setSaving(false);
    }
  }

  return (
    <Card title="New channel">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Input label="Name" value={name} onChange={setName} placeholder="e.g. Kiddo Anime Tales" required />
          <Input label="Timezone (IANA)" value={timezone} onChange={setTimezone} required />
          <Input label="Primary language" value={primary} onChange={setPrimary} required />
          <Input label="Secondary language" value={secondary} onChange={setSecondary} />
        </div>

        <fieldset>
          <legend className="mb-2 text-xs text-base-400">
            Content categories — what this channel is about
          </legend>
          {catalog.status === "loading" ? (
            <Loading label="Loading categories" />
          ) : catalog.status === "error" ? (
            <ErrorNotice message={catalog.error.message} />
          ) : (
            <div className="flex flex-wrap gap-2">
              {catalog.data.items.map((category) => {
                const active = selected.includes(category.key);
                return (
                  <button
                    key={category.key}
                    type="button"
                    aria-pressed={active}
                    title={`Keywords: ${category.keywords.slice(0, 8).join(", ")}`}
                    onClick={() =>
                      setSelected((current) =>
                        active
                          ? current.filter((c) => c !== category.key)
                          : [...current, category.key],
                      )
                    }
                    className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                      active
                        ? "border-accent-600 bg-accent-600/15 text-accent-400"
                        : "border-base-600 bg-base-850 text-base-400 hover:text-base-200"
                    }`}
                  >
                    {category.label}
                  </button>
                );
              })}
            </div>
          )}
          <p className="mt-2 text-[11px] text-base-500">
            Pick what this channel actually covers. Each channel is matched against
            trends independently, so a kids channel and a technology channel on the same
            account rank the same trend differently.
          </p>
        </fieldset>

        {error && <ErrorNotice message={error} />}
        <Button type="submit" variant="primary" disabled={saving || selected.length === 0}>
          {saving ? "Creating…" : "Create channel"}
        </Button>
      </form>
    </Card>
  );
}

function Input({
  label,
  value,
  onChange,
  ...rest
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value">) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs text-base-400">{label}</span>
      <input
        {...rest}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
      />
    </label>
  );
}
