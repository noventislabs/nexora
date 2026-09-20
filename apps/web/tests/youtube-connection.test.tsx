import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { YouTubeConnectionCard } from "@/components/youtube-connection";
import type { YouTubeConnectionState } from "@/lib/types";

const CHANNEL_ID = "11111111-2222-3333-4444-555555555555";

function state(overrides: Partial<YouTubeConnectionState> = {}): YouTubeConnectionState {
  return {
    status: "not_connected",
    connected: false,
    youtube_channel_id: null,
    youtube_channel_title: null,
    youtube_custom_url: null,
    scopes: [],
    has_analytics_scope: false,
    has_monetary_scope: false,
    connected_at: null,
    last_refreshed_at: null,
    token_expires_at: null,
    last_error: null,
    public_channel_id: null,
    public_channel_title: null,
    public_verified_at: null,
    capabilities: {
      upload: false,
      channel_analytics: false,
      revenue: false,
      public_read: false,
    },
    oauth_app: {
      status: "AVAILABLE",
      provider: "youtube",
      detail: "OAuth client configured",
      missing_settings: [],
      metadata: {},
    },
    data_api_key: {
      status: "AVAILABLE",
      provider: "youtube_data_api",
      detail: "Data API key configured",
      missing_settings: [],
      metadata: {},
    },
    note: "A channel id identifies a channel but grants nothing.",
    ...overrides,
  };
}

/** Replaces the network, not the component. Each test declares the API's answer. */
function mockApi(payload: YouTubeConnectionState) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })),
  );
}

beforeEach(() => {
  document.cookie = "nexora_csrf=fixture-csrf";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("YouTubeConnectionCard", () => {
  it("shows NOT CONFIGURED with the missing settings when the OAuth app has no credentials", async () => {
    mockApi(
      state({
        oauth_app: {
          status: "NOT CONFIGURED",
          provider: "youtube",
          detail: "Create an OAuth 2.0 Web application client in Google Cloud Console.",
          missing_settings: ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"],
          metadata: {},
        },
      }),
    );

    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    const panel = await screen.findByTestId("not-configured");
    expect(panel).toHaveTextContent("YOUTUBE_CLIENT_ID");
    expect(panel).toHaveTextContent("YOUTUBE_CLIENT_SECRET");
    // No connect button is offered for something this deployment cannot do.
    expect(screen.queryByRole("button", { name: /connect youtube/i })).not.toBeInTheDocument();
  });

  it("offers a Google sign-in and promises never to ask for the password", async () => {
    mockApi(state());
    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    expect(await screen.findByRole("button", { name: /connect youtube/i })).toBeInTheDocument();
    expect(screen.getByText(/NEXORA never sees it/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("shows NOT CONNECTED when the channel has no consent", async () => {
    mockApi(state());
    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    const pills = await screen.findAllByTestId("status-pill");
    expect(pills[0]).toHaveTextContent("NOT CONNECTED");
  });

  it("does not claim upload access from a linked public channel alone", async () => {
    mockApi(
      state({
        public_channel_id: "UCSuPzUb-cVXp5AiiX58sxA0",
        public_channel_title: "TEST FIXTURE Channel",
        capabilities: {
          upload: false,
          channel_analytics: false,
          revenue: false,
          public_read: true,
        },
      }),
    );

    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    // The public link is visible…
    expect(await screen.findByText(/UCSuPzUb-cVXp5AiiX58sxA0/)).toBeInTheDocument();
    // …and the card still says the channel is not connected for uploading.
    const pills = screen.getAllByTestId("status-pill");
    expect(pills[0]).toHaveTextContent("NOT CONNECTED");
    expect(screen.getByRole("button", { name: /connect youtube/i })).toBeInTheDocument();
  });

  it("lists each capability separately, marking the ones not granted", async () => {
    mockApi(
      state({
        status: "connected",
        connected: true,
        youtube_channel_id: "UCFIXTURECHANNELID000000",
        youtube_channel_title: "TEST FIXTURE Channel",
        has_analytics_scope: true,
        capabilities: {
          upload: true,
          channel_analytics: true,
          revenue: false,
          public_read: false,
        },
      }),
    );

    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    expect(await screen.findByTestId("capability-upload")).toHaveTextContent("GRANTED");
    expect(screen.getByTestId("capability-channel_analytics")).toHaveTextContent("GRANTED");
    // Revenue is a separate consent, and it was not given.
    expect(screen.getByTestId("capability-revenue")).toHaveTextContent("NOT GRANTED");
    expect(screen.getByTestId("capability-public_read")).toHaveTextContent("NOT GRANTED");
  });

  it("explains a revoked connection instead of showing it as connected", async () => {
    mockApi(
      state({
        status: "revoked",
        last_error: "The refresh token was rejected by Google.",
      }),
    );

    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    expect(await screen.findByText(/revoked or expired/i)).toBeInTheDocument();
    expect(screen.getByText("The refresh token was rejected by Google.")).toBeInTheDocument();
  });

  it("reports the public Data API as NOT CONFIGURED without inventing statistics", async () => {
    mockApi(
      state({
        data_api_key: {
          status: "NOT CONFIGURED",
          provider: "youtube_data_api",
          detail: "A server API key is required to read public YouTube data.",
          missing_settings: ["YOUTUBE_API_KEY"],
          metadata: {},
        },
      }),
    );

    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    await waitFor(() => expect(screen.getByText("YOUTUBE_API_KEY")).toBeInTheDocument());
    expect(screen.queryByLabelText(/youtube channel id/i)).not.toBeInTheDocument();
  });

  it("asks for a channel id and says plainly that it grants no upload access", async () => {
    mockApi(state());
    render(<YouTubeConnectionCard channelId={CHANNEL_ID} />);

    expect(await screen.findByLabelText(/youtube channel id/i)).toBeInTheDocument();
    expect(screen.getByText(/grants no upload access/i)).toBeInTheDocument();
  });
});
