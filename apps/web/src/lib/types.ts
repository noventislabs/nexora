/** Shapes returned by the NEXORA API. */

/**
 * A number the backend may or may not actually have.
 *
 * `value === null` means the system genuinely does not know — the UI must render
 * `—` with the reason, never `0`.
 */
export type MaybeMetric = {
  value: number | null;
  available: boolean;
  unavailable_reason: string | null;
};

export type ProviderStatus =
  | "HEALTHY"
  | "DEGRADED"
  | "UNHEALTHY"
  | "AVAILABLE"
  | "UNAVAILABLE"
  | "CONNECTED"
  | "NOT CONNECTED"
  | "NOT CONFIGURED";

export type Availability = {
  status: ProviderStatus;
  provider: string | null;
  detail: string;
  missing_settings: string[];
  metadata: Record<string, unknown>;
};

export type ComponentHealth = {
  name: string;
  status: ProviderStatus;
  detail: string;
  latency_ms: number | null;
  metadata: Record<string, unknown>;
};

export type SystemHealth = {
  status: ProviderStatus;
  checked_at: string;
  components: ComponentHealth[];
};

export type Capabilities = Record<string, Availability>;

export type CurrentUser = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  onboarding_completed: boolean;
};

export type Channel = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  primary_language: string;
  secondary_language: string | null;
  timezone: string;
  categories: string[];
  is_active: boolean;
  created_at: string | null;
  youtube?: Availability;
};

export type AutomationSettings = {
  mode: "assisted" | "semi_autonomous" | "autonomous";
  autopilot_enabled: boolean;
  auto_publish_enabled: boolean;
  require_human_approval: boolean;
  max_videos_per_day: number;
  max_videos_per_week: number;
  min_interval_minutes: number;
  publish_window_start_hour: number;
  publish_window_end_hour: number;
  preferred_publish_hour: number;
  timezone: string;
  min_quality_score: number;
  min_originality_score: number;
  max_copyright_risk: "low" | "medium" | "high";
  block_on_unknown_license: boolean;
  require_fact_check_pass: boolean;
  emergency_stop: boolean;
  emergency_stop_at: string | null;
  emergency_stop_reason: string | null;
  daily_scan_enabled: boolean;
  daily_scan_hour: number;
};

export type DashboardOverview = {
  channel: { id: string; name: string; timezone: string; categories: string[] };
  autopilot: {
    enabled: boolean;
    mode: string;
    auto_publish_enabled: boolean;
    require_human_approval: boolean;
    emergency_stop: boolean;
    emergency_stop_reason: string | null;
    max_videos_per_day: number;
  };
  today: {
    window_start: string;
    window_end: string;
    timezone: string;
    trending_topics: MaybeMetric;
    ideas_generated: MaybeMetric;
    scripts_ready: MaybeMetric;
    videos_rendering: MaybeMetric;
    scheduled: MaybeMetric;
    published: MaybeMetric;
  };
  channel_summary: {
    name: string;
    youtube: Availability;
    subscribers: MaybeMetric;
    views: MaybeMetric;
    videos: MaybeMetric;
    revenue: MaybeMetric;
    last_updated: string | null;
    data_source: string | null;
    data_age_seconds: number | null;
  };
  pipeline: Record<string, number>;
  queue: Record<string, number>;
  pending_approvals: number;
  open_ideas: number;
};

export type Job = {
  id: string;
  type: string;
  queue: string;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED";
  attempts: number;
  max_attempts: number;
  permanent_failure: boolean;
  channel_id: string | null;
  content_project_id: string | null;
  created_at: string | null;
  available_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
};

export type AuditEntry = {
  id: string;
  actor_type: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  summary: string | null;
  created_at: string | null;
};

export type Paged<T> = { items: T[]; total: number; limit: number; offset: number };

// --------------------------------------------------------------------- trends

/** How current an item is, derived from real timestamps. */
export type Freshness = {
  state: "FRESH" | "STALE" | "UNKNOWN";
  age_seconds: number | null;
  basis: "published_at" | "discovered_at" | null;
};

export type ScoreComponent = {
  key: string;
  label: string;
  weight: number;
  value: number | null;
  basis: string;
  available: boolean;
  rating: "High" | "Medium" | "Low" | "UNKNOWN";
};

export type ScoreBreakdown = {
  score: number | null;
  available: boolean;
  unavailable_reason: string | null;
  competition_level: "low" | "medium" | "high" | "unknown";
  available_weight: number;
  components: ScoreComponent[];
  method: string;
  context?: Record<string, unknown>;
  derived_from?: Record<string, unknown>;
};

export type Trend = {
  id: string;
  title: string;
  summary: string | null;
  url: string | null;
  source: { id: string; name: string; kind: string };
  category: string | null;
  language: string | null;
  author: string | null;
  region: string | null;
  published_at: string | null;
  discovered_at: string | null;
  freshness: Freshness;
  /** Only metrics the upstream actually returned. An absent key means "unknown". */
  engagement: Record<string, number>;
  corroboration_count: number;
  duplicate_of_id: string | null;
  opportunity_score: number | null;
  score_breakdown: ScoreBreakdown | null;
  scored_at: string | null;
};

export type TrendSource = {
  id: string;
  kind: "rss" | "youtube_data_api" | "reddit";
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  reliability: number;
  region: string | null;
  min_interval_minutes: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_item_count: number | null;
  consecutive_failures: number;
  next_allowed_at: string | null;
  due_now: boolean;
  availability: Availability;
};

export type TrendSourcesResponse = {
  items: TrendSource[];
  total: number;
  providers: Record<string, Availability>;
  supported_kinds: string[];
};

export type SourceScanResult = {
  source_id: string;
  source_name: string;
  kind: string;
  status: "SUCCESS" | "FAILED" | "SKIPPED" | "NOT_CONFIGURED";
  fetched: number;
  stored: number;
  duplicates: number;
  error: string | null;
  warnings: string[];
};

export type ScanResponse =
  | { mode: "queued"; job_id: string; status: string }
  | {
      mode: "inline";
      started_at: string;
      finished_at: string;
      duration_seconds: number;
      fetched: number;
      stored: number;
      duplicates: number;
      sources: SourceScanResult[];
    };

export type TopicCandidate = {
  id: string;
  title: string;
  angle: string;
  audience: string | null;
  category: string | null;
  why_now: string | null;
  risks: string[];
  sources: {
    trending_topic_id: string;
    title: string;
    url: string | null;
    source_name: string;
    source_kind: string;
    published_at: string | null;
  }[];
  opportunity_score: number | null;
  score_breakdown: ScoreBreakdown | null;
  competition_level: "low" | "medium" | "high" | "unknown";
  evidence_count: number;
  evidence_source_kinds: string[];
  newest_evidence_at: string | null;
  oldest_evidence_at: string | null;
  status: "proposed" | "approved" | "rejected" | "saved" | "converted";
  generated_by: { provider: string | null; model: string | null };
  generation_run_id: string | null;
  created_at: string | null;
  decided_at: string | null;
  decision_note: string | null;
};

export type ScoringModel = {
  name: string;
  range: [number, number];
  not_a_prediction: string;
  formula: string;
  minimum_available_weight: number;
  unavailable_rule: string;
  weights: Record<string, number>;
  velocity_reference_per_hour: Record<string, number>;
  components: Record<string, string>;
};
