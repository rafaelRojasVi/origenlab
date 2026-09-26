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
  created_at: string | null;
  updated_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
  organization: { organization_id: string; name: string | null; confirmation: string | null } | null;
  other_organizations: { organization_id: string; name: string; role: string }[];
  contact: {
    source: "crm_participant" | "gmail_recipient";
    name: string | null;
    address: string | null;
    others: number;
  } | null;
  quotes: QuoteCard[];
  quote_numbers: string[];
  revision_count: number;
  latest_revision: RevisionCard | null;
  drive_folder: { source: "archive_ledger"; folder_id: string; url: string } | null;
  attention: Attention[];
  status: "blocked" | "pending" | "ok";
  next_action: { text: string; source: "suggested"; due_at: string | null };
}

export interface PipelineResponse {
  items: OpportunityCardData[];
  total: number;
  drive_configured: boolean;
}

export interface ProvidersResponse {
  on_cases: { organization_id: string; name: string; confirmation: string; role: string; cases: number }[];
  candidates: { domain: string; trade_name: string | null; resolution: string; mentions: number }[];
}

export interface CampaignSummary {
  campaign_id: string;
  name: string;
  status: string;
  subject: string | null;
  approved_at: string | null;
  created_at: string | null;
  first_sent_at: string | null;
  last_sent_at: string | null;
  recipients_by_state: Record<string, number>;
  send_attempts: { submission_state: string; delivery_state: string; count: number }[];
  replies_recorded: number;
}

export interface MarketingResponse {
  campaigns: CampaignSummary[];
  contact_controls: { kind: string; scope: string; count: number }[];
  replies_note: string;
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
  limit: number;
  offset: number;
}
