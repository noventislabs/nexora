import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChannelProfileEditor } from "@/components/channel-profile";
import type { ChannelProfile, ProfileOptions } from "@/lib/types";

const CHANNEL_ID = "11111111-2222-3333-4444-555555555555";

function profile(overrides: Partial<ChannelProfile> = {}): ChannelProfile {
  const primary = overrides.primary_categories ?? ["kids", "anime"];
  return {
    channel_id: CHANNEL_ID,
    audience_description: null,
    primary_categories: primary,
    secondary_categories: [],
    audience_classification: null,
    country_region: null,
    primary_language: "en",
    secondary_languages: [],
    translation_enabled: false,
    short_form_enabled: false,
    long_form_enabled: true,
    preferred_duration_seconds: null,
    target_videos_per_week: null,
    brand_voice: null,
    preferred_topics: [],
    blocked_topics: [],
    content_exclusions: [],
    sensitive_content_restrictions: [],
    profile_completed_at: null,
    is_complete: false,
    incomplete_fields: ["audience_classification"],
    matching_inputs: {
      primary_categories: primary,
      secondary_categories: [],
      preferred_topics: [],
      blocked_topics: [],
      content_exclusions: [],
      sensitive_content_restrictions: [],
      languages: ["en"],
      audience_classification: null,
      has_matchable_configuration: true,
    },
    note: "NEXORA matches trends against this configuration. It does not infer your audience.",
    ...overrides,
  };
}

const OPTIONS: ProfileOptions = {
  categories: [
    { id: "1", key: "kids", label: "Kids", description: null, keywords: ["kids", "cartoon"], audience_hint: "kids", is_builtin: true, is_channel_owned: false, sort_order: 10 },
    { id: "2", key: "anime", label: "Anime", description: null, keywords: ["anime", "manga"], audience_hint: "general", is_builtin: true, is_channel_owned: false, sort_order: 30 },
    { id: "3", key: "gaming", label: "Gaming", description: null, keywords: ["gaming", "console"], audience_hint: "teen", is_builtin: true, is_channel_owned: false, sort_order: 50 },
    { id: "4", key: "business", label: "Business", description: null, keywords: ["market"], audience_hint: "general", is_builtin: true, is_channel_owned: false, sort_order: 130 },
    { id: "5", key: "retro_computing", label: "Retro computing", description: null, keywords: ["amiga"], audience_hint: null, is_builtin: false, is_channel_owned: true, sort_order: 900 },
  ],
  audience_classifications: [
    { value: "kids", label: "Kids" },
    { value: "family", label: "Family" },
    { value: "teen", label: "Teen" },
    { value: "general", label: "General" },
    { value: "mature", label: "Mature" },
  ],
  audience_note:
    "Audience classification is NEXORA's editorial notion of who a channel is for. It is separate from YouTube's made-for-kids declaration, and neither is ever derived from the other.",
};

/** Routes each fetch by URL, so the component's real request shape is exercised. */
function mockApi(data: ChannelProfile) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.includes("/profile/options") ? OPTIONS : data;
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
}

beforeEach(() => {
  document.cookie = "nexora_csrf=fixture-csrf";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ChannelProfileEditor", () => {
  it("says the audience is undeclared and that NEXORA will not infer it", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);

    const notice = await screen.findByTestId("profile-incomplete");
    expect(notice).toHaveTextContent(/will not infer it/i);
    expect(notice).toHaveTextContent(/name or categories/i);
  });

  it("offers every audience classification, defaulting to not declared", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);

    const select = (await screen.findByLabelText(
      /audience classification/i,
    )) as HTMLSelectElement;
    expect(select.value).toBe("");
    const values = Array.from(select.options).map((option) => option.value);
    expect(values).toEqual(["", "kids", "family", "teen", "general", "mature"]);
  });

  it("keeps the audience separate from the made-for-kids declaration", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    expect(
      await screen.findByText(/separate from YouTube's made-for-kids declaration/i),
    ).toBeInTheDocument();
  });

  it("renders categories from the API rather than a hard-coded list", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);

    // A kids/anime channel sees the whole vocabulary, not a business/tech subset.
    expect(await screen.findByRole("button", { name: /gaming/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /business/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retro computing/i })).toBeInTheDocument();
  });

  it("marks a channel-invented category as custom", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    const custom = await screen.findByRole("button", { name: /retro computing/i });
    expect(custom).toHaveTextContent("(custom)");
  });

  it("does not offer a primary category as a secondary one", async () => {
    mockApi(profile({ primary_categories: ["kids"] }));
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);

    await screen.findByRole("button", { name: /gaming/i });
    // "kids" is primary, so it appears as a chip but not as a selectable button.
    expect(screen.queryByRole("button", { name: /^Kids$/i })).not.toBeInTheDocument();
  });

  it("explains that a blocked phrase excludes rather than demotes", async () => {
    mockApi(profile({ blocked_topics: ["gambling"] }));
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);

    expect(await screen.findByText(/excludes an item outright/i)).toBeInTheDocument();
    expect(screen.getByText(/never offered as/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /remove gambling/i }),
    ).toBeInTheDocument();
  });

  it("says what translation being off actually does", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    expect(
      await screen.findByText(/other languages are excluded, because translation is off/i),
    ).toBeInTheDocument();
  });

  it("says what translation being on actually does", async () => {
    mockApi(profile({ translation_enabled: true }));
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    expect(
      await screen.findByText(/flagged as needing translation/i),
    ).toBeInTheDocument();
  });

  it("explains that stored relevance needs re-ranking after a change", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    expect(
      await screen.findByRole("button", { name: /re-rank collected trends/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/take effect on the next scan/i)).toBeInTheDocument();
  });

  it("shows no empty list as zero configured rules", async () => {
    mockApi(profile());
    render(<ChannelProfileEditor channelId={CHANNEL_ID} />);
    await waitFor(() =>
      expect(screen.getAllByText("None configured.").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("0 blocked topics")).not.toBeInTheDocument();
  });
});
