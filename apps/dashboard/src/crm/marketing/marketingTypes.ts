/** Shapes of `/v2/workspace/marketing/*` and the two campaign-draft commands. */

import type { AttemptTotals, CampaignTotals, ContentState, RepliesState, TotalKey } from "../crmTypes";

export interface TaxonomyImage {
  url: string;
  alt: string;
  scope: "modelo-exacto" | "familia-representativa";
  format: string;
}

export interface TaxonomyFamily {
  id: string;
  name: string;
  color: string | null;
}

export interface TaxonomyBrand {
  id: string;
  name: string;
  family_id: string;
  page_url: string;
  aliases: string[];
}

export interface TaxonomyModel {
  id: string;
  brand_id: string;
  family_id: string;
  name: string;
  code: string | null;
  kind: "modelo" | "serie";
  page_url: string;
  image: TaxonomyImage | null;
  aliases: string[];
}

export interface EquipmentTaxonomy {
  source: string;
  site_base_url: string;
  families: TaxonomyFamily[];
  brands: TaxonomyBrand[];
  models: TaxonomyModel[];
}

export interface CampaignContent {
  campaign_id: string;
  name: string;
  status: string;
  subject: string | null;
  preheader: string | null;
  body_html: string | null;
  has_text: boolean;
  version: number;
  max_sends: number;
  recontact_interval_days: number | null;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  database: string;
  content_sha256?: string | null;
  audience_sha256?: string | null;
  audience_policy_version?: string | null;
  audience_frozen_at?: string | null;
}

export interface DraftSaveResult {
  campaign_id: string;
  status: "draft";
  version: number;
  saved_at: string;
  changed_fields: string[];
  storage: { table: string; database: string };
  replayed: boolean;
}

export type InterestBasis = "purchased" | "requested_quotation" | "requested_information" | "inferred_relevance";

export interface AudienceInterest {
  brand_id: string;
  family_id: string;
  model_id: string | null;
  matched_term: string;
  matched_text: string;
  basis: InterestBasis;
  basis_label: string;
  source: {
    kind: "crm_interest" | "quotation_evidence" | "case_title";
    label: string;
    opportunity_id: string | null;
    case_title: string | null;
    quote_numbers: string[];
    source_record_id: string | null;
    interest_id: string | null;
    detail: string | null;
  };
  date: string | null;
  recorded_in_crm: boolean;
  confirmation: "confirmed" | "machine_proposed" | null;
}

export interface Eligibility {
  eligible: boolean;
  reasons: { code: string; label: string }[];
  notes: { code: string; label: string }[];
}

export interface AudiencePerson {
  key: string;
  address: string;
  contact_point_id: string | null;
  person_id: string | null;
  display_name: string | null;
  organization_ids: string[];
  interests: AudienceInterest[];
  other_interest_count: number;
  eligibility: Eligibility;
}

export interface AudienceDestination {
  key: string;
  address: string;
  eligibility: Eligibility;
  via: "recipient_of_case_quotation" | "contact_point_of_institution";
}

export interface AudienceInstitution {
  organization_id: string;
  name: string | null;
  interests: AudienceInterest[];
  other_interest_count: number;
  destinations: AudienceDestination[];
}

export interface AudienceCoverage {
  crm_interest_rows: number;
  crm_interests_matched: number;
  crm_interests_unmatched: number;
  quotation_evidence_records: number;
  quotation_evidence_with_mentions: number;
  quotation_evidence_already_recorded: number;
  case_titles_with_mentions: number;
  recipients_without_contact_point: number;
  brand_linkage: Record<string, { crm: number; evidence_only: number }>;
  review_queue: Record<string, number>;
}

export interface AudienceResponse {
  persons: AudiencePerson[];
  institutions: AudienceInstitution[];
  sending: {
    unique_destinations: number;
    eligible_unique_destinations: number;
    excluded_by_reason: { code: string; label: string; count: number }[];
  };
  coverage: AudienceCoverage;
}

export interface AudienceQuery {
  family_id?: string;
  brand_id?: string;
  model_id?: string;
  organization_id?: string;
  basis?: InterestBasis[];
  recorded?: "crm" | "evidence";
  q?: string;
}

// ── audience freeze ─────────────────────────────────────────────────────────────────────────

export interface FreezeCriteria {
  family_id: string;
  brand_id: string;
  model_id: string;
  organization_id: string;
  bases: InterestBasis[];
  recorded: "" | "crm" | "evidence";
  q: string;
  scope: "persons" | "institutions" | "both";
}

export interface CodeLabel {
  code: string;
  label: string;
}

export interface FreezeEvidence {
  level: "person" | "institution";
  organization_id: string | null;
  brand_id: string;
  family_id: string;
  model_id: string | null;
  basis: InterestBasis;
  source_kind: string;
  opportunity_id: string | null;
  source_record_id: string | null;
  interest_id: string | null;
  recorded_in_crm: boolean;
  observed_at: string | null;
}

export interface FreezeLine {
  brand_id: string;
  line: string;
  brand: string;
  status: "evidenced" | "sin_informacion";
  label: string;
  bases: InterestBasis[];
  latest_observed_at: string | null;
}

export interface FreezeRow {
  key: string;
  address: string;
  display_name: string | null;
  organizations: { organization_id: string; name: string | null }[];
  via: string[];
  evidence: FreezeEvidence[];
  lines: FreezeLine[];
  relevance: "evidenced" | "sin_informacion";
  evidence_observed_at: string | null;
  review_codes: string[];
  inclusion: "included" | "excluded";
  reasons: CodeLabel[];
  note_labels: CodeLabel[];
  /** W12: prior contact is the only reason this destination is out, and W12 is on. */
  recontact_review_required: boolean;
  /** Shown whenever prior_contact is a reason: what the reviewer must see before deciding. */
  prior_contact: PriorContact | null;
}

export interface PriorContact {
  destination: string;
  /** Last accepted send; null for a historical V1 fact with no recorded date. */
  last_contact_at: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  sources: { source: string; reason: string; recorded_at: string | null }[];
}

export interface SendBlocker {
  code: string;
  label: string;
  detail: string;
}

export interface FreezeProblem {
  code: string;
  message: string;
}

export interface FreezePreview {
  campaign_id: string;
  policy_version: string;
  criteria: Record<string, unknown>;
  preview_sha256: string;
  content: {
    subject: string | null;
    preheader: string | null;
    has_html: boolean;
    body_text_chars: number;
    content_sha256: string | null;
    campaign_version: number;
    promises_baja: boolean;
  };
  rows: FreezeRow[];
  review_required: string[];
  review_pending: string[];
  recontact_review: { enabled: boolean; policy_version: string | null; required: string[] };
  malformed_count: number;
  counts: {
    candidates: number;
    rows: number;
    included: number;
    excluded: number;
    malformed_not_stored: number;
    review_required: number;
    recontact_review_required: number;
    excluded_by_reason: (CodeLabel & { count: number })[];
    included_by_line: { brand_id: string; line: string; brand: string; evidenced: number; sin_informacion: number }[];
  };
  problems: FreezeProblem[];
  send_blockers: SendBlocker[];
  freeze_enabled: boolean;
}

export interface ReviewDecision {
  key: string;
  decision: "include" | "exclude";
  note: string;
}

/** W12: one recipient's recontact decision; a bulk selection sends one per recipient. */
export interface RecontactDecision {
  key: string;
  decision: "approve" | "keep_excluded";
  note: string;
  mode: "individual" | "bulk";
}

export interface RecontactSummary {
  enabled: boolean;
  policy_version: string | null;
  review_required: number;
  approved: number;
  kept_excluded: number;
  not_reviewed: number;
  bulk: number;
}

export interface FreezeResult {
  campaign_id: string;
  status: "audience_frozen";
  version: number;
  frozen_at: string;
  policy_version: string;
  content_sha256: string;
  audience_sha256: string;
  counts: { rows: number; included: number; excluded: number; malformed_not_stored: number };
  storage: { tables: string[]; database: string };
  send_blockers: SendBlocker[];
  recontact_review?: RecontactSummary;
  replayed: boolean;
}

export interface FrozenRecipient {
  recipient_id: string;
  address: string;
  organization_name: string | null;
  display_name: string | null;
  frozen_at: string;
  inclusion: "included" | "excluded";
  frozen_reasons: string[];
  frozen_notes: string[];
  relevance: "evidenced" | "sin_informacion";
  interest_evidence: FreezeEvidence[];
  evidence_observed_at: string | null;
  identity_review: { codes: string[]; decision: string; note: string } | null;
  recontact_review: {
    decision: "approve" | "keep_excluded";
    note: string;
    mode: "individual" | "bulk";
    operator_id: string;
    policy_version: string;
    prior_contact: PriorContact;
  } | null;
  recontact_override_at: string | null;
  campaign_version: number;
  content_sha256: string;
  policy_version: string;
  lifecycle_state: string;
  /** Today's refusals from the live send-time contract (`outbound.marketing_contact_refusals`). */
  send_time_refusals?: { code: string; label: string }[];
  /** Frozen as included, refused today: the snapshot predates a «BAJA» or another control. */
  suppressed_since_freeze?: boolean;
}

export interface FrozenSnapshot {
  campaign_id: string;
  name: string;
  status: string;
  version: number;
  audience_frozen_at: string | null;
  audience_policy_version: string | null;
  content_sha256: string | null;
  audience_sha256: string | null;
  recipients: FrozenRecipient[];
  suppressed_since_freeze?: number;
  unsubscribed_since_freeze?: number;
  storage: { table: string; database: string };
  send_blockers: SendBlocker[];
}

/** `GET /v2/workspace/marketing/suppressions` — W10 unsubscribes, read-only. Masked for a viewer. */
export interface SuppressionEntry {
  contact_control_id: string;
  address: string;
  purpose: "all" | "marketing";
  reason: string;
  source: string;
  recorded_at: string;
  baja_messages: number;
  last_observed_at: string | null;
}

/** A «BAJA» from a sender that could not be proven: held for review, blocking its exact address. */
export interface PendingUnsubscribeReview {
  assertion_id: string;
  address: string;
  review_reason: "lineage_missing" | "recipient_mismatch" | string;
  review_reason_label: string;
  grammar_version: string | null;
  policy_version: string | null;
  observed_at: string | null;
  recorded_at: string;
  /** The review's version; a dismissal must quote it (a stale screen is refused). */
  review_sha256?: string;
}

/** The answer to a W10 review decision (confirm or dismiss). */
export interface UnsubscribeReviewResult {
  command: string;
  assertion_id: string;
  was: string;
  outcome: string;
  replayed: boolean;
}

export interface SuppressionsResponse {
  summary: { unsubscribed_addresses: number; baja_messages: number; last_recorded_at: string | null; pending_reviews?: number };
  entries: SuppressionEntry[];
  pending_reviews?: PendingUnsubscribeReview[];
  truncated: boolean;
  frozen_campaigns: {
    campaign_id: string;
    name: string;
    status: string;
    unsubscribed_since_freeze: number;
    pending_review_since_freeze?: number;
    refused_since_freeze: number;
    included_at_freeze: number;
  }[];
  blocks_by_purpose: { kind: string; purpose: string; count: number }[];
  gmail_sync: { automatic: boolean; label: string };
  grammar: { version: string; accepted: string[]; rule: string };
  sender_policy?: { version: string; rule: string };
  apply_enabled: boolean;
  permanent: boolean;
  resubscribe_supported: boolean;
  storage: { table: string; database: string };
}

/** `GET /v2/workspace/marketing/campaigns/{id}/archive` — what was (or will be) sent, as stored. */
export interface CampaignArchive {
  campaign_id: string;
  name: string;
  status: string;
  subject: string | null;
  preheader: string | null;
  version: number;
  content_sha256: string | null;
  content_frozen_at: string | null;
  audience_frozen_at: string | null;
  audience_sha256: string | null;
  audience_policy_version: string | null;
  created_at: string | null;
  origin: "imported_v1" | "native_v2";
  sender_address: string | null;
  sender_name: string | null;
  /** Present only when the content is frozen and its fingerprint recomputes. */
  html: string | null;
  html_state: "archived_verified" | "not_archived" | "not_frozen" | "no_html" | "fingerprint_mismatch";
  recipients_by_state: Record<string, number>;
  send_attempts: { submission_state: string; delivery_state: string; count: number }[];
  send_batches: { day: string; accepted: number; first_accepted_at: string; last_accepted_at: string }[];
  metrics: { opens: number | null; clicks: number | null; note: string };
  storage: { table: string; database: string };
  totals?: CampaignTotals;
  attempt_totals?: AttemptTotals;
  replies?: RepliesState;
  subject_state?: ContentState;
  preheader_state?: ContentState;
  immutable?: boolean;
  immutable_enforced_by_database?: boolean;
}

export interface HistoryRecipient {
  recipient_id: string;
  /** As recorded for sales/admin; `***@dominio` for a viewer. */
  address: string;
  identity: "crm_person" | "historical_address";
  person_id: string | null;
  person_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
  state: string;
  outcome: "sent" | "bounced" | "excluded" | "rejected" | "unsent";
  exclusion_reasons: { code: string; label: string }[];
  attempts: number;
  accepted_attempts: number;
  rejected_attempts: number;
  delivery_confirmed: boolean;
  sent_at: string | null;
  first_sent_at: string | null;
  rejection: string | null;
  bounce: "bounced" | null;
  bounce_class: string | null;
  gmail_url: string | null;
  replies: number;
  last_reply_at: string | null;
  baja: "registered" | "pending_review" | null;
  interests?: AudienceInterest[];
}

export interface RecipientPage {
  campaign_id: string;
  name: string;
  status: string;
  rows: HistoryRecipient[];
  page: number;
  page_size: number;
  total_rows: number;
  pages: number;
  totals: CampaignTotals;
  filters: {
    total: TotalKey;
    reason: string | null;
    identity: "crm_person" | "historical_address" | null;
    q: string | null;
    search_scope: "address_and_names" | "names_only";
  };
  interests_available: boolean;
  storage: { table: string; database: string };
}

export interface RecipientQuery {
  total: TotalKey;
  reason?: string;
  identity?: "crm_person" | "historical_address";
  q?: string;
  page: number;
  page_size: number;
}

export interface ReplyItem {
  id: string;
  kind: "reply" | "baja" | "baja_pending_review";
  class: string;
  class_label: string;
  classified_by: string;
  received_at: string | null;
  association: "recipient" | "send_lineage" | "address_match";
  recipient_id: string | null;
  address: string | null;
  person_name: string | null;
  organization_name: string | null;
  excerpt: string | null;
  gmail_url: string | null;
  resolution?: string;
  review_reason?: string | null;
}

export interface RepliesResponse {
  campaign_id: string;
  name: string;
  status: string;
  items: ReplyItem[];
  counts: { reply: number; baja: number; baja_pending_review: number };
  sync: RepliesState;
  baja_by_address: { recipients: number; label: string };
  unassociated: { baja_without_campaign_lineage: number; label: string };
  excerpts: string;
  gmail_called: false;
  storage: { table: string; database: string };
}

export interface AuditEvent {
  seq: number;
  event_type: string;
  recorded_at: string | null;
  actor_kind: string;
  actor_name: string | null;
  payload: Record<string, unknown>;
}

export interface AuditResponse {
  campaign_id: string;
  name: string;
  status: string;
  version: number;
  row: {
    created_at: string | null;
    updated_at: string | null;
    approved_at: string | null;
    content_frozen_at: string | null;
    audience_frozen_at: string | null;
  };
  origin: {
    kind: "imported_v1" | "native_v2";
    source_record_id: string | null;
    source_kind: string | null;
    dedupe_key: string | null;
    manifest_name: string | null;
    payload_sha256: string | null;
    acquired_at: string | null;
    review_status: string | null;
  };
  events: AuditEvent[];
  attempt_events: { event_type: string; count: number }[];
  events_note: string | null;
  immutable: boolean;
  immutable_enforced_by_database: boolean;
  immutable_note: string | null;
  actions: [] | null;
  storage: { tables: string[]; database: string };
}

export interface PlanningResult {
  campaign_id: string;
  status: string;
  planning_version: number;
  planned_for_date: string | null;
  planned_for_at: string | null;
  time_zone: string;
  changed: boolean;
  schedules_send: false;
  label: string;
  replayed?: boolean;
}
