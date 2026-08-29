/** Types for apps/api commercial read routes (GET /cases/warm, GET /opportunities/equipment). */

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

export type EquipmentDataSource = "active_current_csv" | "postgres_mirror";

export interface EquipmentOpportunitiesMeta {
  data_source: EquipmentDataSource;
  read_only: boolean;
  count: number;
  source_path: string;
  campaign_mode: string | null;
  reduced_mode: boolean;
  note: string;
}

export interface EquipmentAnexoItem {
  nombre?: string;
  tipo?: string;
  descripcion?: string;
  tamano?: string;
  fecha_adjunto?: string;
  url?: string;
}

export interface EquipmentOpportunityItem {
  priority_rank: number;
  codigo_licitacion: string;
  buyer: string;
  region: string;
  close_date: string;
  equipment_category: string;
  item_description: string;
  next_action: string;
  safe_channel: string;
  supplier_needed: string;
  contact_status: string;
  contact_email: string;
  operator_note: string;
  /** Stable cross-source id from Postgres current view (optional). */
  opportunity_key?: string;
  fecha_publicacion?: string;
  close_at?: string;
  validity_status?: string;
  chilecompra_status?: string;
  chilecompra_status_code?: string;
  api_checked_at_utc?: string;
  source?: string;
  mercado_publico_url?: string;
  title?: string;
  unspsc_code?: string;
  unidad?: string;
  cantidad?: string;
  producto?: string;
  nivel_1?: string;
  nivel_2?: string;
  nivel_3?: string;
  anexos?: EquipmentAnexoItem[];
}

export interface EquipmentOpportunitiesResponse {
  meta: EquipmentOpportunitiesMeta;
  items: EquipmentOpportunityItem[];
}

/** Parsed equipment payload for UI (filesystem paths stripped from meta). */
export interface EquipmentOpportunitiesUiResponse {
  meta: Omit<EquipmentOpportunitiesMeta, "source_path">;
  items: EquipmentOpportunityItem[];
}

export interface WarmCasesQuery {
  days?: number;
  limit?: number;
  category?: string;
  positive_signal_only?: boolean;
  include_noise?: boolean;
}

export interface EquipmentOpportunitiesQuery {
  limit?: number;
  priority?: number;
  next_action?: string;
  safe_channel?: string;
  include_account_intelligence?: boolean;
}
