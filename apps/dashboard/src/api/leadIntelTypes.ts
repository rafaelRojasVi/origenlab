/** UI types for read-only lead research mirror (GET /mirror/leads/*). */

export interface LeadProspectListItemUi {
  prospect_key: string;
  organization_name: string;
  contact_name: string | null;
  email: string | null;
  domain: string | null;
  sector: string | null;
  region: string | null;
  buyer_type: string | null;
  product_angle: string | null;
  final_score: number;
  classification: string;
  status: string;
  spanish_message_angle: string | null;
  recommended_next_action: string | null;
  risk_flags: string | null;
  evidence_url: string | null;
  is_blocked: boolean;
  campaign_bucket: string | null;
  source_type: string | null;
  dataset_label: string | null;
  gmail_first_contacted_at: string | null;
  gmail_last_contacted_at: string | null;
  gmail_sent_count: number | null;
  gmail_received_count: number | null;
  gmail_latest_subject_safe: string | null;
  commercial_action_bucket: string | null;
  operational_next_action: string | null;
}

export interface LeadProspectEvidenceUi {
  evidence_kind: string;
  evidence_url: string | null;
  evidence_note: string | null;
  source: string | null;
  confidence: string | null;
}

export interface LeadProspectRecommendationUi {
  campaign_bucket: string | null;
  recommended_message_angle: string | null;
  recommended_next_action: string | null;
  why_this_lead: string | null;
  suggested_subject: string | null;
  suggested_body_preview: string | null;
  safety_note: string | null;
}

export interface LeadProspectBlockReasonUi {
  reason_code: string;
  reason_label: string | null;
}

export interface LeadProspectDetailUi {
  prospect_key: string;
  organization_name: string;
  contact_name: string | null;
  email: string | null;
  domain: string | null;
  sector: string | null;
  region: string | null;
  buyer_type: string | null;
  likely_need: string | null;
  product_angle: string | null;
  evidence_url: string | null;
  evidence_note: string | null;
  source: string | null;
  final_score: number;
  confidence: string | null;
  classification: string;
  spanish_message_angle: string | null;
  risk_flags: string | null;
  block_or_review_reason: string | null;
  recommended_next_action: string | null;
  status: string;
  campaign_bucket: string | null;
  is_blocked: boolean;
  source_type: string | null;
  dataset_label: string | null;
  gmail_first_contacted_at: string | null;
  gmail_last_contacted_at: string | null;
  gmail_sent_count: number | null;
  gmail_received_count: number | null;
  gmail_latest_subject_safe: string | null;
  commercial_action_bucket: string | null;
  operational_next_action: string | null;
}

export interface LeadProspectsListUi {
  table_available: boolean;
  items: LeadProspectListItemUi[];
  total: number;
  data_source: string;
  read_only: boolean;
  disclaimer: string;
}

export interface LeadProspectDetailResponseUi {
  table_available: boolean;
  prospect: LeadProspectDetailUi | null;
  evidence: LeadProspectEvidenceUi[];
  recommendation: LeadProspectRecommendationUi | null;
  block_reasons: LeadProspectBlockReasonUi[];
  data_source: string;
  read_only: boolean;
  disclaimer: string;
}

export interface LeadResearchSummaryUi {
  table_available: boolean;
  total: number;
  review_count: number;
  blocked_count: number;
  net_new_safe: number;
  gmail_historico: number;
  followup_antiguo: number;
  caso_activo: number;
  public_tender_review: number;
  same_domain_review: number;
  research_needed: number;
  ready_to_contact: number;
  already_contacted: number;
  needs_email_enrichment: number;
  tender_opportunity: number;
  review_history: number;
  followup_eligible: number;
  data_source: string;
  read_only: boolean;
  disclaimer: string;
}

export type ContactScope =
  | "contacted"
  | "followup"
  | "active"
  | "deepsearch"
  | "net_new"
  | "blocked";

export type ProspectOriginFilter =
  | ""
  | "deepsearch"
  | "gmail_historico"
  | "followup_antiguo"
  | "caso_activo"
  | "legacy_2016_2019"
  | "same_domain_contacted_review"
  | "research_only_contact_needed"
  | "public_tender_review"
  | "blocked";

export interface LeadProspectsListQuery {
  q?: string;
  classification?: string;
  source_type?: string;
  blocked_only?: boolean;
  sector?: string;
  region?: string;
  buyer_type?: string;
  campaign_bucket?: string;
  min_score?: number;
  include_blocked?: boolean;
  contact_scope?: ContactScope;
  limit?: number;
}
