/** Types for apps/api commercial read routes (GET /cases/warm). */

export type WarmCaseCategory =
  | "client_opportunity"
  | "client_response"
  | "supplier_quote_received"
  | "supplier_followup"
  | "payment_admin"
  | "logistics_admin"
  | "internal_admin"
  | "system_noise"
  | "bounce_problem"
  | "deal_evidence_candidate"
  | "quote_sent"
  | "waiting_supplier"
  | "waiting_client"
  | "campaign_outreach"
  | "waiting_campaign_reply"
  | "auto_acknowledgement"
  | "client_reply"
  | "supplier_reply"
  | "bounce"
  | "opportunity"
  | "auto_reply"
  | "vendor_logistics";

export type WarmCaseStatus = "new" | "open" | "waiting" | "quoted" | "problem";

export interface WarmCasesMeta {
  data_source: "sqlite" | "postgres_mirror";
  read_only: boolean;
  reduced_mode: boolean;
  count: number;
  enrichment_available: boolean;
  note: string;
}

export interface WarmCaseItem {
  case_id: string;
  last_email_id: number;
  last_seen_at: string | null;
  account_name: string;
  contact_email: string;
  subject: string;
  category: WarmCaseCategory;
  status: WarmCaseStatus;
  next_action: string;
  equipment_signal: string;
  snippet: string;
  gmail_url: string | null;
  /** Collapsed duplicate emails in the same supplier/thread group (API default 1). */
  grouped_email_count?: number;
}

export interface WarmCasesResponse {
  meta: WarmCasesMeta;
  items: WarmCaseItem[];
}

export interface WarmCasesQuery {
  days?: number;
  limit?: number;
  category?: string;
  positive_signal_only?: boolean;
  include_noise?: boolean;
}
