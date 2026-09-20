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
  /** Channel-independent. Identical for every channel that can see this row. */
  signal_score: number | null;
  signal_breakdown: ScoreBreakdown | null;
  scored_at: string | null;
  /** "shared" rows are ingested once for the account and ranked per channel. */
  scope: "channel" | "shared";
  /** This channel's verdict. `null` until the row has been ranked for it. */
  relevance: ChannelRelevance | null;
  /** Signal combined with relevance, for the channel that asked. */
  opportunity_score: number | null;
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

// -------------------------------------------------------------------- research

export type Classification = "FACT" | "CLAIM" | "ANALYSIS" | "OPINION" | "UNKNOWN";

export type ResearchStatement = {
  statement: string;
  classification: Classification;
  document_indices: number[];
  attributed_to?: string;
};

export type ResearchDocument = {
  index: number;
  id: string;
  origin: string;
  title: string;
  url: string | null;
  publisher: string | null;
  author: string | null;
  published_at: string | null;
  fetched_at: string | null;
  /** ALLOWED | BLOCKED_BY_ROBOTS | NOT_ATTEMPTED | DISABLED | FAILED */
  fetch_decision: string;
  fetch_note: string | null;
  http_status: number | null;
  word_count: number | null;
  truncated: boolean;
  has_text: boolean;
  error: string | null;
};

export type ResearchConflict = {
  subject: string;
  positions: { position: string; document_indices: number[] }[];
};

export type Research = {
  id: string;
  topic_candidate_id: string;
  status: string;
  summary: string | null;
  key_facts: ResearchStatement[];
  claims: ResearchStatement[];
  statistics: {
    value: string;
    what_it_measures: string;
    as_of: string | null;
    document_indices: number[];
  }[];
  entities: Record<string, string[]>;
  conflicts: ResearchConflict[];
  uncertainties: string[];
  sources: { index: number; title: string; url: string | null; publisher: string | null }[];
  document_count: number;
  provider: string | null;
  model: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  documents?: ResearchDocument[];
};

// --------------------------------------------------------------------- content

export type ContentProject = {
  id: string;
  channel_id: string;
  title: string;
  status: string;
  video_format: "long_form" | "short";
  target_duration_seconds: number;
  language: string;
  topic_candidate_id: string | null;
  research_id: string | null;
  current_script_version_id: string | null;
  approval_status: string;
  approved_at: string | null;
  rejection_reason: string | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type ScriptSection = {
  kind: string;
  heading: string;
  narration: string;
  document_indices: number[];
};

export type ScriptVersion = {
  id: string;
  version: number;
  word_count: number;
  estimated_duration_seconds: number;
  /** Explains that the runtime is derived, not measured. */
  duration_basis: string;
  provider: string | null;
  model: string | null;
  created_at: string | null;
  notes: string | null;
  section_count: number;
  source_references: { index: number; title: string; url: string | null }[];
  sections?: ScriptSection[];
  narration_text?: string;
  plain_text?: string;
};

export type Originality = {
  score: number;
  checked_documents: number;
  longest_verbatim_run_words: number;
  overlap_ratio?: number;
  matches: { document_index: number; words: number; excerpt: string }[];
  scope: string;
  /** False when no meaningful comparison was possible — not a pass. */
  conclusive: boolean;
};

export type FactCheckClaim = {
  assertion: string;
  narration_sentence: string | null;
  document_indices: number[];
  verdict: "SUPPORTED" | "NEEDS_REVIEW" | "UNSUPPORTED";
  reasons: string[];
};

export type FactCheck = {
  id: string;
  status: "PASS" | "REVIEW" | "FAIL" | "NOT_RUN";
  script_version_id: string | null;
  supported: number;
  needs_review: number;
  unsupported: number;
  claims: FactCheckClaim[];
  contradictions: { kind: string; subject?: string; detail?: string; note?: string }[];
  provider: string | null;
  model: string | null;
  created_at: string | null;
  blocks_publishing: boolean;
};

export type ProjectDetail = ContentProject & {
  research: Research | null;
  script: {
    current_version: number;
    status: string;
    versions: ScriptVersion[];
    current: ScriptVersion | null;
  };
  fact_check: FactCheck | null;
};

// ----------------------------------------------------------------------- media

export type VoiceStatus = Availability & {
  voices: { id: string; name: string; languages: string[] }[];
  provides_timings?: boolean;
  status_detail: {
    provider: string;
    model?: string;
    quota: {
      characters_used: number | null;
      character_limit: number | null;
      characters_remaining: number | null;
      tier?: string | null;
    } | null;
    quota_note?: string;
  } | null;
};

export type VoiceJob = {
  id: string;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED";
  provider: string | null;
  voice_id: string | null;
  language: string;
  audio_asset_id: string | null;
  character_count: number | null;
  duration_seconds: number | null;
  /** Says the duration was measured, not estimated. Null when unmeasured. */
  duration_basis: string | null;
  timing_source: "provider" | "estimated" | null;
  timing_is_estimated: boolean;
  cue_count: number;
  mime_type: string | null;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
};

export type MediaAsset = {
  id: string;
  kind: string;
  source: string;
  source_url: string | null;
  source_provider: string | null;
  license_type: string | null;
  license_status: "PERMITTED" | "LICENSE UNKNOWN" | "PROHIBITED";
  license_url: string | null;
  attribution: string | null;
  usage_permission_note: string | null;
  acquired_at: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  checksum_sha256: string | null;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  content_project_id: string | null;
  created_at: string | null;
  /** True whenever the licence is anything other than PERMITTED. */
  blocks_autonomous_publishing: boolean;
};

export type AssetListing = Paged<MediaAsset> & {
  kinds: string[];
  license_types: string[];
};

export type RenderJob = {
  id: string;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED";
  progress_percent: number;
  output_asset_id: string | null;
  subtitle_asset_id: string | null;
  duration_seconds: number | null;
  duration_basis: string | null;
  resolution: string | null;
  output_bytes: number | null;
  ffmpeg_version: string | null;
  command_digest: string | null;
  error: string | null;
  log_excerpt: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type Scene = {
  index: number;
  kind: string;
  heading: string;
  start: number;
  end: number;
  duration: number;
  cue_indices: number[];
  document_indices: number[];
  background_asset_id: string | null;
};

export type RenderListing = {
  items: RenderJob[];
  total: number;
  scene_plan: Scene[];
  aspect_ratio?: string;
  resolution?: string;
  subtitle_burn_in?: boolean;
};

export type ProjectThumbnail = {
  id: string;
  asset_id: string | null;
  generator: string;
  concept: string | null;
  headline: string | null;
  status: "generated" | "approved" | "rejected";
  width: number | null;
  height: number | null;
  error: string | null;
  created_at: string | null;
  decided_at: string | null;
  license_status?: string;
  size_bytes?: number | null;
  mime_type?: string | null;
};

// ------------------------------------------------------------------ Phase 5: YouTube

/**
 * What an established connection can actually do.
 *
 * These are four separate permissions, not one. Identifying a channel publicly grants
 * `public_read` and nothing else — in particular it never grants `upload`.
 */
export type YouTubeCapabilities = {
  upload: boolean;
  channel_analytics: boolean;
  revenue: boolean;
  public_read: boolean;
};

export type YouTubeConnection = {
  status: "not_connected" | "connected" | "revoked" | "error";
  connected: boolean;
  youtube_channel_id: string | null;
  youtube_channel_title: string | null;
  youtube_custom_url: string | null;
  scopes: string[];
  has_analytics_scope: boolean;
  has_monetary_scope: boolean;
  connected_at: string | null;
  last_refreshed_at: string | null;
  token_expires_at: string | null;
  last_error: string | null;
  public_channel_id: string | null;
  public_channel_title: string | null;
  public_verified_at: string | null;
  capabilities: Partial<YouTubeCapabilities>;
};

export type YouTubeConnectionState = YouTubeConnection & {
  /** Whether this deployment has OAuth client credentials at all. */
  oauth_app: Availability;
  /** Whether this deployment has a server API key for public reads. */
  data_api_key: Availability;
  note: string;
};

export type PublicChannel = {
  channel_id: string;
  title: string;
  custom_url: string | null;
  description: string | null;
  published_at: string | null;
  country: string | null;
  /** `null` when the owner hides it. Never rendered as 0. */
  subscriber_count: number | null;
  subscriber_count_hidden: boolean;
  view_count: number | null;
  video_count: number | null;
  thumbnail_url: string | null;
  source: string;
  scope_note: string;
};

export type PublicChannelStatus = {
  linked: boolean;
  channel_id: string | null;
  title: string | null;
  verified_at: string | null;
  note: string;
};

export type YouTubeVideo = {
  id: string;
  youtube_video_id: string;
  url: string;
  title: string | null;
  privacy_status: string | null;
  upload_status: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  thumbnail_url: string | null;
  last_synced_at: string | null;
  content_project_id: string | null;
};

export type MetadataVersion = {
  id: string;
  version: number;
  title: string;
  title_length: number;
  description: string;
  description_length: number;
  tags: string[];
  tags_total_chars: number;
  category_id: string | null;
  default_language: string | null;
  made_for_kids: boolean | null;
  provider: string | null;
  model: string | null;
  created_at: string | null;
  limits: { title: number; description: number; tags_total_chars: number };
};

/** One publishing gate and whether it currently allows publishing. */
export type PreflightGate = {
  key: string;
  label: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
};

export type QualityCheck = {
  id: string;
  kind: string;
  status: "PASS" | "WARN" | "FAIL" | "UNKNOWN";
  score: number | null;
  checks: PreflightGate[];
  details: Record<string, unknown>;
  created_at: string | null;
  blocks_publishing: boolean;
};

export type CopyrightCheck = {
  id: string;
  status: "PASS" | "WARN" | "FAIL" | "UNKNOWN";
  risk_level: string;
  unknown_license_count: number;
  prohibited_count: number;
  findings: { label: string; detail: string; severity?: string }[];
  created_at: string | null;
  blocks_publishing: boolean;
};

export type Preflight = {
  can_publish: boolean;
  gates: PreflightGate[];
  blockers: string[];
  warnings: string[];
  quality: QualityCheck | null;
  copyright: CopyrightCheck | null;
};

export type PublishJob = {
  id: string;
  content_project_id: string;
  status: string;
  privacy_status: string;
  scheduled_for: string | null;
  authorized_by: string;
  approved_at: string | null;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string | null;
  permanent_failure: boolean;
  last_error: string | null;
  youtube_video_id: string | null;
  youtube_url: string | null;
  upload_bytes: number | null;
  verified_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  preflight: Record<string, unknown>;
  created_at: string | null;
};

// -------------------------------------------------- Phase 6: channel intelligence

export type ContentCategory = {
  id: string;
  key: string;
  label: string;
  description: string | null;
  /** What the relevance engine matches on. Shown so a match is never a black box. */
  keywords: string[];
  audience_hint: string | null;
  is_builtin: boolean;
  is_channel_owned: boolean;
  sort_order: number;
};

export type AudienceClassification = "kids" | "family" | "teen" | "general" | "mature";

export type ChannelProfile = {
  channel_id: string;
  audience_description: string | null;
  primary_categories: string[];
  secondary_categories: string[];
  /** `null` means undeclared. It is never inferred from the channel's name. */
  audience_classification: AudienceClassification | null;
  country_region: string | null;
  primary_language: string;
  secondary_languages: string[];
  translation_enabled: boolean;
  short_form_enabled: boolean;
  long_form_enabled: boolean;
  preferred_duration_seconds: number | null;
  target_videos_per_week: number | null;
  brand_voice: string | null;
  preferred_topics: string[];
  blocked_topics: string[];
  content_exclusions: string[];
  sensitive_content_restrictions: string[];
  profile_completed_at: string | null;
  is_complete: boolean;
  incomplete_fields: string[];
  matching_inputs: {
    primary_categories: string[];
    secondary_categories: string[];
    preferred_topics: string[];
    blocked_topics: string[];
    content_exclusions: string[];
    sensitive_content_restrictions: string[];
    languages: string[];
    audience_classification: string | null;
    has_matchable_configuration: boolean;
  };
  note: string;
};

export type ProfileOptions = {
  categories: ContentCategory[];
  audience_classifications: { value: AudienceClassification; label: string }[];
  audience_note: string;
};

export type RelevanceStatus =
  | "RELEVANT"
  | "LOW_RELEVANCE"
  | "EXCLUDED"
  | "INSUFFICIENT_DATA";

export type MatchedCategory = {
  category: string;
  tier: "primary" | "secondary";
  keywords: string[];
};

export type ChannelRelevance = {
  status: RelevanceStatus;
  /** `null` when there was nothing to match against — never rendered as 0. */
  relevance_score: number | null;
  score: number | null;
  score_status: "SCORED" | "INSUFFICIENT_DATA" | "EXCLUDED";
  matched_categories: MatchedCategory[];
  matched_preferences: { preference: string; matched: string }[];
  excluded_by_rules: { rule: string; value: string; detail: string }[];
  relevance_reasons: { code: string; detail: string }[];
  available_source_count: number;
  freshness: string | null;
  computed_at: string | null;
  score_breakdown: Record<string, unknown> | null;
};

export type RelevanceExplanation = {
  channel_id: string;
  channel_name: string;
  trending_topic_id: string;
  title: string;
  signal_score: number | null;
  matching_inputs: ChannelProfile["matching_inputs"];
  relevance: ChannelRelevance;
};
