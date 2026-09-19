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
