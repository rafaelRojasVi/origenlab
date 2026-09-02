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
  | "superseded";

/** The Cotizaciones Kanban lane a durable quote is in, derived server-side
 * from its current revision's status — never a stored field, never
 * computed client-side. Preparación was removed as a separate lane
 * (CRM-Q2B): draft/adjustments_requested/pending_approval all derive
 * "review" — sub-state is shown via revision_status alone. */
export type BoardStage =
  | "review"
  | "approved_to_send"
  | "sent_follow_up";

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
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  drive_workspace: CustomerQuoteDriveWorkspace;
}

export interface CustomerQuoteRevisionTransitionCommand {
  expected_version: number;
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
