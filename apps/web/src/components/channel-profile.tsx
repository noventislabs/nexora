"use client";

import { useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { useApi } from "@/components/use-api";
import {
  Button,
  Card,
  ErrorNotice,
  Loading,
  UNKNOWN_PLACEHOLDER,
} from "@/components/primitives";
import type { ChannelProfile, ContentCategory, ProfileOptions } from "@/lib/types";

/**
 * Editor for who a channel is for.
 *
 * The category list comes from the API, not from a constant in this file: the
 * vocabulary is stored data that a channel can extend, and a hard-coded list here
 * would quietly put the product back to supporting one kind of channel.
 */
export function ChannelProfileEditor({ channelId }: { channelId: string }) {
  const profile = useApi<ChannelProfile>(`/api/channels/${channelId}/profile`, [channelId]);
  const options = useApi<ProfileOptions>(
    `/api/channels/${channelId}/profile/options`,
    [channelId],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reranked, setReranked] = useState<string | null>(null);

  async function patch(body: Partial<ChannelProfile>) {
    setBusy(true);
    setError(null);
    setReranked(null);
    try {
      await apiFetch(`/api/channels/${channelId}/profile`, { method: "PATCH", body });
      profile.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the profile.");
    } finally {
      setBusy(false);
    }
  }

  async function rerank() {
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<{ evaluated: number }>(
        `/api/channels/${channelId}/relevance/recompute`,
        { method: "POST" },
      );
      setReranked(
        `Re-ranked ${result.evaluated} collected trend${result.evaluated === 1 ? "" : "s"} against the updated profile.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not re-rank.");
    } finally {
      setBusy(false);
    }
  }

  if (profile.status === "loading" || options.status === "loading") {
    return <Loading label="Loading channel profile" />;
  }
  if (profile.status === "error") return <ErrorNotice message={profile.error.message} />;
  if (options.status === "error") return <ErrorNotice message={options.error.message} />;

  const data = profile.data;
  const categories = options.data.categories;

  return (
    <div className="space-y-4">
      {error && <ErrorNotice message={error} />}
      {reranked && (
        <div className="rounded-lg border border-ok-500/30 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {reranked}
        </div>
      )}

      <Card title="Audience">
        {!data.is_complete && (
          <p
            data-testid="profile-incomplete"
            className="mb-3 rounded-lg border border-warn-500/30 bg-warn-500/10 px-3 py-2 text-xs text-warn-500"
          >
            This channel has not declared who it is for. NEXORA will not infer it from
            the channel&apos;s name or categories, so trend matching runs without it
            until you choose.
          </p>
        )}

        <label className="block max-w-xs">
          <span className="mb-1.5 block text-xs text-base-400">Audience</span>
          <select
            aria-label="Audience classification"
            value={data.audience_classification ?? ""}
            disabled={busy}
            onChange={(event) =>
              patch({
                audience_classification: (event.target.value ||
                  null) as ChannelProfile["audience_classification"],
              })
            }
            className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
          >
            <option value="">Not declared</option>
            {options.data.audience_classifications.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <p className="mt-2 text-[11px] text-base-500">{options.data.audience_note}</p>

        <TextArea
          label="Who watches this channel"
          value={data.audience_description}
          placeholder="e.g. Parents watching with children under eight."
          disabled={busy}
          onSave={(value) => patch({ audience_description: value })}
        />
        <TextArea
          label="Brand voice"
          value={data.brand_voice}
          placeholder="e.g. Warm, slow-paced, never sarcastic."
          disabled={busy}
          onSave={(value) => patch({ brand_voice: value })}
        />
      </Card>

      <Card title="Content categories">
        <p className="mb-3 text-xs text-base-400">
          Primary categories are set on the channel itself. Secondary categories here
          count as a weaker match. A category matches a trend when one of its stored
          keywords appears in the item&apos;s text.
        </p>

        <div className="mb-4">
          <p className="mb-1.5 text-xs text-base-500">Primary</p>
          <div className="flex flex-wrap gap-1.5">
            {data.primary_categories.length === 0 ? (
              <span className="text-sm text-base-500">{UNKNOWN_PLACEHOLDER}</span>
            ) : (
              data.primary_categories.map((key) => (
                <CategoryChip key={key} category={findCategory(categories, key)} active />
              ))
            )}
          </div>
        </div>

        <p className="mb-1.5 text-xs text-base-500">Secondary</p>
        <div className="flex flex-wrap gap-1.5">
          {categories
            .filter((category) => !data.primary_categories.includes(category.key))
            .map((category) => {
              const active = data.secondary_categories.includes(category.key);
              return (
                <button
                  key={category.key}
                  type="button"
                  aria-pressed={active}
                  disabled={busy}
                  title={`Keywords: ${category.keywords.slice(0, 8).join(", ")}`}
                  onClick={() =>
                    patch({
                      secondary_categories: active
                        ? data.secondary_categories.filter((k) => k !== category.key)
                        : [...data.secondary_categories, category.key],
                    })
                  }
                  className={`rounded-full border px-3 py-1 text-xs transition-colors disabled:opacity-50 ${
                    active
                      ? "border-accent-600 bg-accent-600/15 text-accent-400"
                      : "border-base-600 bg-base-850 text-base-400 hover:text-base-200"
                  }`}
                >
                  {category.label}
                  {category.is_channel_owned && (
                    <span className="ml-1 text-[10px] text-base-500">(custom)</span>
                  )}
                </button>
              );
            })}
        </div>
      </Card>

      <Card title="Topic rules">
        <p className="mb-3 text-xs text-base-400">
          Phrases are matched exactly as typed, on whole words. A blocked phrase
          excludes an item outright — it is not ranked, and it is never offered as
          evidence for a topic.
        </p>
        <PhraseList
          label="Preferred topics"
          values={data.preferred_topics}
          disabled={busy}
          onChange={(values) => patch({ preferred_topics: values })}
        />
        <PhraseList
          label="Blocked topics"
          values={data.blocked_topics}
          disabled={busy}
          danger
          onChange={(values) => patch({ blocked_topics: values })}
        />
        <PhraseList
          label="Content exclusions"
          values={data.content_exclusions}
          disabled={busy}
          danger
          onChange={(values) => patch({ content_exclusions: values })}
        />
        <PhraseList
          label="Sensitive content restrictions"
          values={data.sensitive_content_restrictions}
          disabled={busy}
          danger
          onChange={(values) => patch({ sensitive_content_restrictions: values })}
        />
      </Card>

      <Card title="Formats and languages">
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={busy}
            onClick={() => patch({ long_form_enabled: !data.long_form_enabled })}
          >
            Long-form: {data.long_form_enabled ? "ON" : "OFF"}
          </Button>
          <Button
            disabled={busy}
            onClick={() => patch({ short_form_enabled: !data.short_form_enabled })}
          >
            Shorts: {data.short_form_enabled ? "ON" : "OFF"}
          </Button>
          <Button
            disabled={busy}
            onClick={() => patch({ translation_enabled: !data.translation_enabled })}
          >
            Translation: {data.translation_enabled ? "ON" : "OFF"}
          </Button>
        </div>
        <p className="mt-3 text-xs text-base-400">
          Working languages: {data.matching_inputs.languages.join(", ")}.{" "}
          {data.translation_enabled
            ? "Items in other languages are kept and flagged as needing translation."
            : "Items in other languages are excluded, because translation is off."}
        </p>
        <PhraseList
          label="Additional languages"
          values={data.secondary_languages}
          disabled={busy}
          onChange={(values) => patch({ secondary_languages: values })}
        />
      </Card>

      <Card title="Apply to collected trends">
        <p className="text-sm text-base-400">
          Relevance is stored per trend, so changes here take effect on the next scan.
          Re-rank now to apply them to trends already collected.
        </p>
        <div className="mt-3">
          <Button variant="primary" disabled={busy} onClick={rerank}>
            {busy ? "Re-ranking…" : "Re-rank collected trends"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function findCategory(categories: ContentCategory[], key: string): ContentCategory {
  return (
    categories.find((category) => category.key === key) ?? {
      id: key,
      key,
      label: key.replace(/_/g, " "),
      description: null,
      keywords: [],
      audience_hint: null,
      is_builtin: false,
      is_channel_owned: false,
      sort_order: 999,
    }
  );
}

function CategoryChip({
  category,
  active = false,
}: {
  category: ContentCategory;
  active?: boolean;
}) {
  return (
    <span
      title={
        category.keywords.length > 0
          ? `Keywords: ${category.keywords.slice(0, 8).join(", ")}`
          : undefined
      }
      className={`rounded-full border px-3 py-1 text-xs ${
        active
          ? "border-accent-600 bg-accent-600/15 text-accent-400"
          : "border-base-600 bg-base-850 text-base-400"
      }`}
    >
      {category.label}
    </span>
  );
}

function PhraseList({
  label,
  values,
  disabled,
  danger = false,
  onChange,
}: {
  label: string;
  values: string[];
  disabled: boolean;
  danger?: boolean;
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  return (
    <div className="mt-4">
      <p className="mb-1.5 text-xs text-base-400">{label}</p>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {values.length === 0 && (
          <span className="text-xs text-base-500">None configured.</span>
        )}
        {values.map((value) => (
          <button
            key={value}
            type="button"
            disabled={disabled}
            aria-label={`Remove ${value}`}
            onClick={() => onChange(values.filter((item) => item !== value))}
            className={`rounded-full border px-2.5 py-0.5 text-xs disabled:opacity-50 ${
              danger
                ? "border-danger-500/40 bg-danger-500/10 text-danger-500"
                : "border-base-600 bg-base-850 text-base-300"
            }`}
          >
            {value} ×
          </button>
        ))}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const phrase = draft.trim().toLowerCase();
          if (!phrase || values.includes(phrase)) return;
          onChange([...values, phrase]);
          setDraft("");
        }}
        className="flex gap-2"
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label={`Add to ${label}`}
          placeholder="Add a phrase"
          className="w-full max-w-xs rounded-lg border border-base-600 bg-base-850 px-3 py-1.5 text-sm text-base-100 outline-none focus:border-accent-600"
        />
        <Button type="submit" disabled={disabled || draft.trim().length === 0}>
          Add
        </Button>
      </form>
    </div>
  );
}

function TextArea({
  label,
  value,
  placeholder,
  disabled,
  onSave,
}: {
  label: string;
  value: string | null;
  placeholder?: string;
  disabled: boolean;
  onSave: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  const dirty = draft !== (value ?? "");

  return (
    <div className="mt-4">
      <label className="block">
        <span className="mb-1.5 block text-xs text-base-400">{label}</span>
        <textarea
          value={draft}
          rows={2}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
        />
      </label>
      {dirty && (
        <div className="mt-2">
          <Button disabled={disabled} onClick={() => onSave(draft)}>
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
