"use client";

import { useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  ErrorNotice,
  Loading,
  NotConfigured,
  StatusPill,
  UNKNOWN_PLACEHOLDER,
} from "@/components/primitives";
import type {
  PublicChannel,
  YouTubeCapabilities,
  YouTubeConnectionState,
} from "@/lib/types";

/**
 * The two YouTube capabilities, kept visually apart on purpose.
 *
 * Pasting a channel id identifies a channel and unlocks its *public* statistics. It is
 * not permission to upload. Only Google's consent screen grants that, and the card
 * never lets the first look like the second.
 */
export function YouTubeConnectionCard({
  channelId,
  onChanged,
}: {
  channelId: string;
  onChanged?: () => void;
}) {
  const state = useApi<YouTubeConnectionState>(
    `/api/youtube/connection?channel_id=${channelId}`,
    [channelId],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  if (state.status === "loading") return <Loading label="Loading YouTube connection" />;
  if (state.status === "error") return <ErrorNotice message={state.error.message} />;

  const connection = state.data;

  async function startConnect() {
    setBusy("connect");
    setError(null);
    try {
      const result = await apiFetch<{ authorization_url: string }>(
        `/api/youtube/connect?channel_id=${channelId}`,
        { method: "POST", body: { include_analytics: true, include_monetary: false } },
      );
      // Leaving the app is the point: the password is typed on Google's page, never here.
      window.location.href = result.authorization_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the connection.");
      setBusy(null);
    }
  }

  async function disconnect() {
    setBusy("disconnect");
    setError(null);
    try {
      await apiFetch(`/api/youtube/disconnect?channel_id=${channelId}`, { method: "POST" });
      state.refresh();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not disconnect.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      {error && <ErrorNotice message={error} />}

      <Card
        title="YouTube upload access"
        action={
          <StatusPill
            status={connection.connected ? "CONNECTED" : "NOT CONNECTED"}
            label={connection.connected ? "CONNECTED" : "NOT CONNECTED"}
          />
        }
      >
        {connection.oauth_app.status === "NOT CONFIGURED" ? (
          <NotConfigured
            feature="Google OAuth application"
            detail={connection.oauth_app.detail}
            missing={connection.oauth_app.missing_settings}
          />
        ) : connection.connected ? (
          <>
            <dl className="space-y-1.5 text-sm">
              <Row
                label="Channel"
                value={connection.youtube_channel_title ?? UNKNOWN_PLACEHOLDER}
              />
              <Row
                label="Channel id"
                value={connection.youtube_channel_id ?? UNKNOWN_PLACEHOLDER}
              />
              <Row
                label="Connected"
                value={
                  connection.connected_at
                    ? new Date(connection.connected_at).toLocaleString()
                    : UNKNOWN_PLACEHOLDER
                }
              />
            </dl>
            <CapabilityList capabilities={connection.capabilities} />
            <div className="mt-4">
              <Button variant="danger" onClick={disconnect} disabled={busy !== null}>
                {busy === "disconnect" ? "Disconnecting…" : "Disconnect"}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-base-300">
              Sign in with Google to let NEXORA upload to your channel. You type your
              password on Google&apos;s page — NEXORA never sees it, and never asks for it.
            </p>
            {connection.status === "revoked" && (
              <p className="mt-2 text-xs text-warn-500">
                The previous authorization was revoked or expired. Reconnect to restore
                upload access.
              </p>
            )}
            {connection.last_error && (
              <p className="mt-2 text-xs text-danger-500">{connection.last_error}</p>
            )}
            <div className="mt-4">
              <Button variant="primary" onClick={startConnect} disabled={busy !== null}>
                {busy === "connect" ? "Redirecting…" : "Connect YouTube"}
              </Button>
            </div>
          </>
        )}
      </Card>

      <PublicChannelCard
        channelId={channelId}
        connection={connection}
        onLinked={() => {
          state.refresh();
          onChanged?.();
        }}
      />
    </div>
  );
}

function CapabilityList({ capabilities }: { capabilities: Partial<YouTubeCapabilities> }) {
  const rows: [keyof YouTubeCapabilities, string][] = [
    ["upload", "Upload videos"],
    ["channel_analytics", "Channel analytics (impressions, watch time, retention)"],
    ["revenue", "Revenue figures"],
    ["public_read", "Public channel statistics"],
  ];

  return (
    <ul className="mt-4 space-y-1.5 border-t border-base-800 pt-3">
      {rows.map(([key, label]) => {
        const granted = capabilities[key] === true;
        return (
          <li key={key} className="flex items-center gap-2 text-xs">
            <span
              data-testid={`capability-${key}`}
              className={`font-mono ${granted ? "text-ok-500" : "text-base-500"}`}
            >
              {granted ? "GRANTED" : "NOT GRANTED"}
            </span>
            <span className="text-base-400">{label}</span>
          </li>
        );
      })}
    </ul>
  );
}

function PublicChannelCard({
  channelId,
  connection,
  onLinked,
}: {
  channelId: string;
  connection: YouTubeConnectionState;
  onLinked: () => void;
}) {
  const [identifier, setIdentifier] = useState("");
  const [result, setResult] = useState<PublicChannel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function link(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch<{ channel: PublicChannel }>(
        `/api/youtube/public-channel?channel_id=${channelId}`,
        { method: "POST", body: { channel_identifier: identifier } },
      );
      setResult(response.channel);
      onLinked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not verify that channel id.");
    } finally {
      setBusy(false);
    }
  }

  if (connection.data_api_key.status === "NOT CONFIGURED") {
    return (
      <NotConfigured
        feature="Public channel statistics"
        detail={connection.data_api_key.detail}
        missing={connection.data_api_key.missing_settings}
      />
    );
  }

  const channel = result;

  return (
    <Card title="Public channel (read only)">
      <p className="text-sm text-base-400">
        A channel id identifies your channel and shows the statistics YouTube shows
        everyone. It grants no upload access and no private analytics.
      </p>

      {connection.public_channel_id && (
        <p className="mt-3 text-sm text-base-200">
          Linked: {connection.public_channel_title ?? connection.public_channel_id}{" "}
          <span className="font-mono text-xs text-base-500">
            ({connection.public_channel_id})
          </span>
        </p>
      )}

      <form onSubmit={link} className="mt-3 flex flex-wrap items-end gap-2">
        <label className="block flex-1">
          <span className="mb-1.5 block text-xs text-base-400">
            Channel id (starts with UC, 24 characters)
          </span>
          <input
            value={identifier}
            onChange={(event) => setIdentifier(event.target.value)}
            placeholder="UC…"
            aria-label="YouTube channel id"
            className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 font-mono text-sm text-base-100 outline-none focus:border-accent-600"
          />
        </label>
        <Button type="submit" disabled={busy || identifier.trim().length === 0}>
          {busy ? "Verifying…" : "Verify and link"}
        </Button>
      </form>

      {error && (
        <div className="mt-3">
          <ErrorNotice message={error} />
        </div>
      )}

      {channel && (
        <dl className="mt-4 space-y-1.5 border-t border-base-800 pt-3 text-sm">
          <Row label="Title" value={channel.title} />
          <Row
            label="Subscribers"
            value={
              channel.subscriber_count === null
                ? `${UNKNOWN_PLACEHOLDER} (hidden by the owner)`
                : channel.subscriber_count.toLocaleString()
            }
          />
          <Row
            label="Total views"
            value={channel.view_count?.toLocaleString() ?? UNKNOWN_PLACEHOLDER}
          />
          <Row
            label="Videos"
            value={channel.video_count?.toLocaleString() ?? UNKNOWN_PLACEHOLDER}
          />
          <p className="pt-2 text-xs text-base-500">{channel.scope_note}</p>
        </dl>
      )}
    </Card>
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
