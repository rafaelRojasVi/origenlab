/**
 * Durable customer-quote types (CRM-Q1).
 *
 * PostgreSQL is the durable CRM truth; Google Drive is the human working
 * surface. The dashboard only ever renders validated safe references — it
 * never invents quote numbers, statuses, or Drive locations.
 */

import type { SalesOpportunityStage } from "./commercialOperationsTypes";

export type CustomerQuoteStatus = "draft";

export type QuoteOrigin = "generated" | "adopted";

export type RevisionStatus =
  | "draft"
  | "pending_approval"
  | "adjustments_requested"
  | "approved"
  | "sent"
  | "superseded"
  | "closed_won"
  | "closed_null";

/** The two closure outcomes (CRM-Q2B). "null" here means the quote is void/
 * cancelled -- NOT necessarily a lost sale; never conflated with the
 * separate sales-opportunity "lost" stage. */
export type QuoteOutcome = "won" | "null";

/** The Cotizaciones Kanban lane a durable quote is in, derived server-side
 * from its current revision's status — never a stored field, never
 * computed client-side. Preparación was removed as a separate lane
 * (CRM-Q2B): draft/adjustments_requested/pending_approval all derive
 * "review" — sub-state is shown via revision_status alone. */
export type BoardStage =
  | "review"
  | "approved_to_send"
  | "sent_follow_up"
  | "closed";

export type QuoteProvisioningStatus = "pending" | "ready" | "folder_ready" | "failed";

export interface CustomerQuoteDriveWorkspace {
  provider: "google_drive";
  provisioning_status: QuoteProvisioningStatus;
  folder_id: string | null;
  /** Validated https Google URL or null — never rendered otherwise. */
  folder_web_url: string | null;
  sheet_file_id: string | null;
  /** Validated https Google URL or null — never rendered otherwise. */
  sheet_web_url: string | null;
  /** Redacted failure category slug (e.g. drive_unavailable) or null. */
  failure_category: string | null;
  attempt_count: number;
  version: number;
  /** Server-computed: false while an attempt actively owns the lease --
   * an immediate retry would only conflict. */
  retryable: boolean;
  lease_expires_at: string | null;
  requested_at: string | null;
  completed_at: string | null;
}

export interface CustomerQuote {
  quote_id: string;
  sales_opportunity_id: string;
  quote_number: string;
  document_number: string;
  quote_origin: QuoteOrigin;
  sales_opportunity_title: string;
  status: CustomerQuoteStatus;
  version: number;
  latest_revision_number: number;
  revision_status: RevisionStatus;
  revision_updated_by: string;
  revision_updated_at: string;
  board_stage: BoardStage;
  /** null while the quote is still active; "won"/"null" once closed. */
  quote_outcome: QuoteOutcome | null;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  drive_workspace: CustomerQuoteDriveWorkspace;
}

export interface CustomerQuoteRevisionTransitionCommand {
  expected_version: number;
}

export interface CustomerQuoteCloseCommand {
  expected_version: number;
  outcome: QuoteOutcome;
}

export interface AdoptCustomerQuoteDriveFolderCommand {
  document_number: string;
  quote_number: string;
  folder_id: string;
  folder_web_url: string;
}

export interface CustomerQuoteEvent {
  event_id: string;
  event_type: string;
  actor_key: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CustomerQuoteEventListResponse {
  meta: { count: number };
  items: CustomerQuoteEvent[];
}

export interface CustomerQuoteListResponse {
  meta: { count: number };
  items: CustomerQuote[];
}

export interface CustomerQuoteReadResponse {
  item: CustomerQuote;
}

export interface RetryCustomerQuoteDriveWorkspaceCommand {
  expected_version: number;
}

export interface CustomerQuoteGlobalItem {
  quote: CustomerQuote;
  sales_opportunity_stage: SalesOpportunityStage;
  sales_opportunity_owner_key: string;
  organization_display_name: string | null;
  contact_display_name: string | null;
  contact_primary_email: string | null;
  next_task_title: string | null;
  next_task_due_at: string | null;
}

export interface CustomerQuoteGlobalListMeta {
  count: number;
  total_count: number;
  limit: number;
  offset: number;
}

export interface CustomerQuoteGlobalListResponse {
  meta: CustomerQuoteGlobalListMeta;
  items: CustomerQuoteGlobalItem[];
}

/**
 * A Drive-only pending workspace (CRM-Q1D follow-up): a folder that exists
 * in the human Pendientes working directory but has no durable
 * customer_quote record yet. Never carries a quote_id, opportunity,
 * lifecycle status, revision, or provisioning-retry field -- those only
 * exist once a durable quote is created.
 */
export interface DrivePendingQuoteItem {
  folder_id: string;
  folder_name: string;
  /** Validated https Google URL — never rendered otherwise. */
  folder_web_url: string | null;
  /** Conservatively parsed CN/document identifier, or null when ambiguous. */
  document_identifier: string | null;
  created_time: string | null;
  modified_time: string | null;
}

export interface CustomerQuoteDrivePendingListMeta {
  count: number;
}

export interface CustomerQuoteDrivePendingListResponse {
  meta: CustomerQuoteDrivePendingListMeta;
  items: DrivePendingQuoteItem[];
}

/** One row of the unified Cotizaciones operator queue. */
export type QuoteQueueRow =
  | { kind: "crm"; item: CustomerQuoteGlobalItem }
  | { kind: "drive_pending"; item: DrivePendingQuoteItem };

/**
 * Intake evidence resolution ("Incorporar al CRM", CRM-Q2B). Read-only,
 * ephemeral -- nothing here is durable CRM truth until the operator
 * confirms and submits the adoption command. Confidence is always one of
 * three reason-coded levels, never a numeric ML score.
 */
export type IntakeConfidence = "confirmed_durable_match" | "possible_match" | "unresolved";

export interface IntakeEvidenceItem {
  source: string;
  reason: string;
  detail: string;
}

export interface IntakeOrganizationAlternate {
  organization_id: string;
  display_name: string;
}

export interface IntakeOrganizationCandidate {
  organization_id: string | null;
  display_name: string;
  confidence: IntakeConfidence;
  evidence: IntakeEvidenceItem[];
  /** Populated only when confidence is "possible_match" with 2+ durable
   * candidates -- the operator picks, nothing is auto-selected. */
  alternates: IntakeOrganizationAlternate[];
}

export interface IntakeContactCandidate {
  contact_id: string | null;
  display_name: string | null;
  email: string | null;
  confidence: IntakeConfidence;
  evidence: IntakeEvidenceItem[];
}

export interface IntakeOpportunityCandidate {
  sales_opportunity_id: string | null;
  title: string;
  confidence: IntakeConfidence;
}

export interface CustomerQuoteIntakeResolution {
  document_number_candidate: string | null;
  document_number_conflict: boolean;
  organization: IntakeOrganizationCandidate | null;
  contacts: IntakeContactCandidate[];
  opportunity: IntakeOpportunityCandidate | null;
  /** Always false in this slice -- quote_number is never auto-resolved;
   * the operator always confirms it explicitly. */
  quote_number_resolved: false;
}
