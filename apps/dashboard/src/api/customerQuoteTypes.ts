/**
 * Durable customer-quote types (CRM-Q1).
 *
 * PostgreSQL is the durable CRM truth; Google Drive is the human working
 * surface. The dashboard only ever renders validated safe references — it
 * never invents quote numbers, statuses, or Drive locations.
 */

import type { SalesOpportunityStage } from "./commercialOperationsTypes";

export type CustomerQuoteStatus = "draft";

export type QuoteProvisioningStatus = "pending" | "ready" | "failed";

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
  sales_opportunity_title: string;
  status: CustomerQuoteStatus;
  version: number;
  latest_revision_number: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  drive_workspace: CustomerQuoteDriveWorkspace;
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
