import type { SupplierCandidate } from "./supplierCandidates";
import type { AudienceInterest } from "./marketing/marketingTypes";

/**
 * Types for `GET /v2/workspace/*` (apps/api `v2/crm_workspace.py`).
 *
 * Two sources travel side by side and are never merged: the CRM (durable truth) and the Drive
 * archive ledgers (`source: "archive_ledger"`), which say where a quotation PDF is in Drive.
 */

export type Provenance = "imported" | "partial" | "not_imported" | "no_write_path";

export interface EntityCount {
  key: string;
  count: number;
  provenance: Provenance;
  note: string;
}

export interface WorkspaceOverview {
  entities: EntityCount[];
  opportunities_by_stage: Record<string, number>;
  organizations_by_confirmation: Record<string, number>;
  contact_points_linked: { organization: number; person: number };
  assertions: { kind: string; resolution: string; count: number }[];
  drive_archive: {
    configured: boolean;
    documents: number;
    revisions_with_drive_file: number;
    revisions_total: number;
  };
}

export interface DriveLinkRef {
  source: "archive_ledger";
  ledger: string;
  document_sha256: string;
  file_id: string;
  file_url: string;
  folder_id: string | null;
  folder_url: string | null;
  case_key: string | null;
  quote_number: string | null;
  revision: number | null;
  original_filename: string | null;
  archive_status: string | null;
}

export interface GmailRef {
  source_record_id: string | null;
  message_id: string;
  thread_id: string | null;
  url: string;
  subject: string | null;
}

export interface RevisionCard {
  revision_id: string;
  revision_no: number;
  status: string;
  origin: string | null;
  sent_at: string | null;
  superseded_by_revision_no: number | null;
  is_active: boolean;
  document: { sha256: string; filename: string | null } | null;
  gmail: GmailRef | null;
  drive: DriveLinkRef | null;
  quote_number: string;
}

/** `GET /v2/workspace/opportunities/{id}/mail-documents` — what «Registrar cotización» picks from. */
export interface CaseMailDocument {
  sha256: string;
  filename: string | null;
  /** The capture's quote-number readings of the file name: a hint, never a recorded number. */
  cn_tokens: string[];
  /** Already a quote revision (anywhere: one document is one revision), or null. */
  recorded: { quote_number: string; revision_no: number; on_this_case: boolean } | null;
}

export interface CaseMailMessage {
  source_record_id: string;
  subject: string | null;
  sent_at: string | null;
  documents: CaseMailDocument[];
}

export interface CaseMailDocumentsResponse {
  opportunity_id: string;
  messages: CaseMailMessage[];
}

/** A cross-thread match is ONLY a suggestion until an operator links the evidence. */
export interface CrossThreadQuoteCandidate {
  source_record_id: string;
  subject: string | null;
  sent_at: string;
  quote_token: string;
  filename: string;
  document_sha256: string;
  gmail_url: string;
  reason: string;
  recorded_elsewhere: boolean;
  other_cases_on_quote_thread: { opportunity_id: string; title: string }[];
}

export interface CaseQuoteCandidatesResponse {
  opportunity_id: string;
  candidates: CrossThreadQuoteCandidate[];
}

/** Read-only PO suggestion, matched conservatively against a quotation's recipient. */
export interface CasePurchaseOrderCandidate {
  source_record_id: string;
  gmail_url: string;
  subject: string | null;
  sent_at: string | null;
  filename: string;
  document_sha256: string;
  purchase_order_number: string | null;
  reason: string;
}

export interface CasePurchaseOrderCandidatesResponse {
  opportunity_id: string;
  candidates: CasePurchaseOrderCandidate[];
}

/** One email on a case's thread, as «último contacto» shows it. */
export interface MailTouch {
  /** Display header evidence; not a confirmed CRM person. Never a mailbox address. */
  sender_name?: string | null;
  at: string;
  subject: string | null;
  url: string | null;
}

export interface QuoteCard {
  quote_id: string;
  quote_number: string;
  number_origin: string | null;
  revisions: RevisionCard[];
}

export interface Attention {
  code: string;
  label: string;
  blocking: boolean;
}

export interface OpportunityCardData {
  opportunity_id: string;
  title: string;
  stage: string;
  /** The case version a case command compares against. Absent from an older API. */
  version?: number | null;
  created_at: string | null;
  updated_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
  organization: {
    organization_id: string;
    name: string | null;
    confirmation: string | null;
    /** The organization version «Confirmar institución» compares against. */
    version?: number | null;
  } | null;
  /** Human confirmation of the requesting-institution role on THIS case.
   * Undefined only for an older API response; null means no current role.
   * This is independent of organization.confirmation.
   */
  requesting_institution_confirmation?: string | null;
  /** Machine-suggested 'mentioned' relationships, not a confirmed requester.
   * Each row ID allows an operator to make a recorded case-role decision.
   */
  pending_institution_mentions?: {
    opportunity_organization_id: string;
    organization_id: string;
    name: string;
  }[];
  other_organizations: { organization_id: string; name: string; role: string }[];
  contact: {
    source: "crm_participant" | "gmail_recipient";
    name: string | null;
    address: string | null;
    others: number;
    /** Fingerprint of the bare address; joins the card to its equipment interests when masked. */
    address_ref?: string | null;
  } | null;
  quotes: QuoteCard[];
  quote_numbers: string[];
  revision_count: number;
  latest_revision: RevisionCard | null;
  drive_folder: { source: "archive_ledger"; folder_id: string; url: string } | null;
  /**
   * «Último contacto»: the newest email OrigenLab sent and the newest it received on the case's
   * Gmail threads. Optional so an older API (and old test fixtures) still parse.
   */
  last_contact?: { outbound: MailTouch | null; inbound: MailTouch | null };
  attention: Attention[];
  status: "blocked" | "pending" | "ok";
  /** Open `crm.task` rows (W11), earliest due first. Absent from an older API. */
  open_tasks?: OpenTask[];
  /** The earliest open task (`source: "task"`), or a deterministic suggestion. */
  next_action: { text: string; source: "suggested" | "task"; due_at: string | null };
}

export interface OpenTask {
  task_id: string;
  title: string;
  /** ISO 8601, UTC. */
  due_at: string;
  version: number;
  owner: string | null;
  /** ISO 8601, UTC: when the task was scheduled. Absent from an older API. */
  created_at?: string | null;
}

export interface PipelineResponse {
  items: OpportunityCardData[];
  total: number;
  drive_configured: boolean;
}

export interface SupplierDirectoryEntry {
  brand_id: string;
  name: string;
  page_url: string | null;
  family: { id: string; name: string; color: string | null };
  model_count: number;
  crm_organizations: { organization_id: string; name: string; confirmation: string | null; roles: string[]; cases: number }[];
  /** Machine candidates whose domain or trade name names the brand. A hint, never a promotion. */
  candidate_hints: SupplierCandidate[];
}

export interface ProvidersResponse {
  /** The six catalogue brands, curated by the website — not detected. Older APIs omit it. */
  directory?: SupplierDirectoryEntry[];
  on_cases: { organization_id: string; name: string; confirmation: string; role: string; cases: number }[];
  /** Machine candidates in `review.state` form (see supplierCandidates.ts). Never promoted here. */
  candidates: SupplierCandidate[];
}

export interface CampaignSummary {
  campaign_id: string;
  name: string;
  status: string;
  subject: string | null;
  preheader?: string | null;
  /** Whether the stored campaign has HTML content. False means it was never imported. */
  has_html?: boolean;
  /** Per-campaign html_state from the archive read, included in the marketing list when available. */
  html_state?: "archived_verified" | "not_archived" | "not_frozen" | "no_html" | "fingerprint_mismatch" | "sent_html_archived" | "historical_draft" | "not_recovered" | "ambiguous_attribution";
  version?: number;
  approved_at: string | null;
  created_at: string | null;
  updated_at?: string | null;
  first_sent_at: string | null;
  last_sent_at: string | null;
  recipients_by_state: Record<string, number>;
  send_attempts: { submission_state: string; delivery_state: string; count: number }[];
  replies_recorded: number;
  /** Accepted attempts grouped by the day (America/Santiago) they were accepted on. */
  send_batches?: SendBatch[];
  /** Attempts with no acceptance date (rejected, never submitted). */
  attempts_without_date?: number;
  /** Internal planning only; schedules nothing. A Santiago calendar day. */
  planned_for_date?: string | null;
  /** The planned instant in UTC, when a time was chosen. */
  planned_for_at?: string | null;
  planning_version?: number;
  audience_frozen_at?: string | null;
  content_frozen_at?: string | null;
  content_sha256?: string | null;
  /** `imported_v1`: loaded from the V1 send ledger. `native_v2`: written in this CRM. */
  origin?: "imported_v1" | "native_v2";
  sender_address?: string | null;
  sender_name?: string | null;
  equipment_lines?: { family_id: string; source: "audience_criteria" | "name_or_subject"; matched_term: string | null }[];
  /** Recipient totals, one predicate each — the same the recipient list filters by. */
  totals?: CampaignTotals | null;
  attempt_totals?: AttemptTotals | null;
  replies?: RepliesState;
  subject_state?: ContentState;
  /** Campaign safety blocks (WORKFLOWS.md §W13): whether anything refuses this campaign now. */
  hold?: { held: boolean; refusals: { code: string; label: string }[] };
}

export type TotalKey = "audience" | "included" | "sent" | "excluded" | "blocked" | "unsent" | "rejected" | "bounced" | "responses";
export type CampaignTotals = Record<TotalKey, number>;
export type ContentState = "recorded" | "not_imported" | "not_set";

/** Send attempts — not recipients. `accepted` is Gmail's acceptance, not a delivery. */
export interface AttemptTotals {
  attempts: number;
  accepted: number;
  rejected: number;
  other: number;
  delivery_confirmed: number;
  delivery_pending: number;
  delivery_bounced: number;
  rejected_undated: number;
  with_provider_id: number;
  first_accepted_at: string | null;
  last_accepted_at: string | null;
}

/** `not_synced`: nothing was ever stored, so there is no count to show — never a zero. */
export interface RepliesState {
  state: "not_synced" | "partial";
  label: string;
  count: number | null;
}

export interface SendBatch {
  day: string;
  accepted: number;
  first_accepted_at: string;
  last_accepted_at: string;
}

/** A campaign being sent through the V1 systemd lane, declared in apps/api v1_lane_campaigns.json. */
export interface V1LaneCampaign {
  key: string;
  name: string;
  channel: "v1";
  send_days: string[];
  send_time: string;
  promo_until: string;
  /** Clients planned per send day (the runner's wave sizes), same order as `send_days`. */
  clients_per_day?: number[] | null;
  total_clients?: number | null;
  audience_rule?: string | null;
  /** The email as sent, when the API has it (kept outside the repository). */
  html?: string | null;
  subject?: string | null;
}

export interface MarketingResponse {
  campaigns: CampaignSummary[];
  contact_controls: { kind: string; scope: string; count: number }[];
  replies_note: string;
  /** Where campaigns (and drafts) are stored, as the API reports it. */
  storage?: { table: string; database: string };
  authoring?: { drafts_enabled: boolean; freeze_enabled?: boolean; recontact_review_enabled?: boolean; planning_enabled?: boolean };
  time_zone?: string;
  /** Campaigns running through the old V1 systemd lane, not yet in the V2 database. */
  v1_lane_campaigns?: V1LaneCampaign[];
  test_send?: { enabled: boolean; per_hour: number; per_day: number; sender: string };
}

export interface DriveDocument extends DriveLinkRef {
  gmail_url: string | null;
  in_crm: boolean;
  crm: {
    revision_no: number;
    quote_number: string;
    opportunity_id: string;
    opportunity_title: string;
    organization_name: string | null;
  } | null;
  ledger_crm_status: string | null;
}

export interface DriveFolder {
  folder_id: string | null;
  folder_url: string | null;
  case_key: string | null;
  documents: DriveDocument[];
  quote_numbers: string[];
  in_crm: number;
  organization_name: string | null;
}

export interface DriveArchiveResponse {
  configured: boolean;
  source: "archive_ledger";
  ledgers: string[];
  folders: DriveFolder[];
  totals: { folders: number; documents: number; in_crm: number; not_in_crm: number };
  crm_revisions_without_drive_file: { sha256: string; quote_number: string; revision_no: number }[];
}

export interface ReviewResponse {
  archived_not_in_crm: (DriveLinkRef & { ledger_crm_status: string | null; gmail_url: string | null })[];
  open_assertions: { kind: string; resolution: string; count: number }[];
  ambiguous_organizations: {
    assertion_id: string;
    value_norm: string;
    ambiguity_note: string | null;
    source_record_id: string;
  }[];
  drive_configured: boolean;
}

export interface WorkQueueItem {
  kind: string;
  reason: string;
  next_action: string;
  subject_ids: Record<string, string>;
  age_days: number | null;
  label: string | null;
}

export interface WorkQueueResponse {
  items: WorkQueueItem[];
  total: number;
  /** Items per kind across the whole queue, not just this page. */
  counts?: Record<string, number>;
  limit: number;
  offset: number;
}

/** `GET /v2/workspace/mail-sync` — whether the Gmail capture is running (Phase 4a). */
/** One quote number the captured Gmail shows (`/v2/workspace/mail-quote-numbers`). No client data. */
export interface MailQuoteNumber {
  /** As the capture proposed it from an attachment name, e.g. «CN01247» (no year). */
  quote_number: string;
  first_seen_at: string;
  messages: number;
}

export interface MailQuoteNumbersResponse {
  items: MailQuoteNumber[];
}

export interface MailSyncStatus {
  state: "ok" | "late" | "stopped" | "not_started" | "not_configured";
  authorization_state: string | null;
  last_synced_at: string | null;
  minutes_since_sync: number | null;
  late_after_minutes: number;
}

/** One equipment line: a taxonomy family (one brand each), the same six Marketing filters on. */
export interface EquipmentLine {
  family_id: string;
  name: string;
  color: string | null;
  brand_ids: string[];
  crm_people: number;
  address_only: number;
  institutions: number;
}

export interface InterestPerson {
  key: string;
  address: string;
  address_ref: string;
  contact_point_id: string | null;
  person_id: string | null;
  display_name: string | null;
  organization_ids: string[];
  /** A person the CRM records, or only an address seen in historical evidence. */
  link: "crm_person" | "address_only";
  interests: AudienceInterest[];
}

export interface InterestInstitution {
  organization_id: string;
  name: string | null;
  interests: AudienceInterest[];
}

export interface EquipmentInterestsResponse {
  lines: EquipmentLine[];
  persons: InterestPerson[];
  institutions: InterestInstitution[];
}
