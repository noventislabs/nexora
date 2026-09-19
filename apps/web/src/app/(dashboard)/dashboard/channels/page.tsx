"use client";

import { useState } from "react";
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
import type { Channel, Paged } from "@/lib/types";

const CATEGORIES = [
  "business",
  "technology",
  "future",
  "ai",
  "science",
  "digital_economy",
  "global_developments",
] as const;

export default function ChannelsPage() {
  const channels = useApi<Paged<Channel>>("/api/channels");
  const [creating, setCreating] = useState(false);

  if (channels.status === "loading") return <Loading label="Loading channels" />;
  if (channels.status === "error") return <ErrorNotice message={channels.error.message} />;

  return (
    <div className="space-y-5">
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
                <p className="mt-2 text-[11px] text-base-500">
                  Connecting a channel uses Google OAuth. NEXORA never asks for a YouTube
                  password. (Available in Phase 5.)
                </p>
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
  const [name, setName] = useState("NEXORA Global");
  const [timezone, setTimezone] = useState("Asia/Dhaka");
  const [primary, setPrimary] = useState("en");
  const [secondary, setSecondary] = useState("bn");
  const [selected, setSelected] = useState<string[]>(["business", "technology", "ai", "future"]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
          <Input label="Name" value={name} onChange={setName} required />
          <Input label="Timezone (IANA)" value={timezone} onChange={setTimezone} required />
          <Input label="Primary language" value={primary} onChange={setPrimary} required />
          <Input label="Secondary language" value={secondary} onChange={setSecondary} />
        </div>

        <fieldset>
          <legend className="mb-2 text-xs text-base-400">Content categories</legend>
          <div className="flex flex-wrap gap-2">
            {CATEGORIES.map((category) => {
              const active = selected.includes(category);
              return (
                <button
                  key={category}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    setSelected((current) =>
                      active ? current.filter((c) => c !== category) : [...current, category],
                    )
                  }
                  className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                    active
                      ? "border-accent-600 bg-accent-600/15 text-accent-400"
                      : "border-base-600 bg-base-850 text-base-400 hover:text-base-200"
                  }`}
                >
                  {category.replace(/_/g, " ")}
                </button>
              );
            })}
          </div>
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
