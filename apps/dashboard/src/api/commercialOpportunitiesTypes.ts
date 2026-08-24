export type CommercialOpportunityDataSource =
  | "sqlite_pr3"
  | "postgres_mirror";

export interface CommercialOpportunitiesQuery {
  limit?: number;
  offset?: number;
  canonical_stage?: string;
  record_kind?: string;
  review_status?: string;
  account_id?: string;
  primary_contact_id?: string;
}

export interface CommercialOpportunitiesMeta {
  data_source: CommercialOpportunityDataSource;
  read_only: boolean;
  count: number;
  total_count: number;
  limit: number;
  offset: number;
  reduced_mode: boolean;
  note: string;
}

export interface CommercialOpportunityItem {
  opportunity_id: string;
  record_kind: string;

  account_id: string | null;
  primary_contact_id: string | null;

  contact_display_email: string | null;
  account_display_domain: string | null;

  source_kind: string;
  source_key: string;
  deal_key: string | null;

  canonical_stage: string;
  source_stage: string;
  stage_reason_code: string;
  stage_confidence: string;
  stage_is_current: boolean;
  stage_is_terminal: boolean;

  stage_evidence_at: string | null;
  stage_evidence_id: string | null;
  first_activity_at: string | null;
  last_activity_at: string | null;

  identity_link_status: string;
  review_status: string;
  synced_at: string | null;
}

export interface CommercialOpportunityEvent {
  event_id: string;
  opportunity_id: string;
  canonical_event_type: string;
  source_event_type: string;
  event_at: string | null;
  source_table: string;
  source_record_id: string;
  source_email_id: number | null;
  source_attachment_id: number | null;
  confidence: string;
  operator_confirmed: boolean;
  detail_json: unknown;
  synced_at: string | null;
}

export interface CommercialOpportunityEvidence {
  evidence_id: string;
  opportunity_id: string;
  subject_kind: string;
  source_table: string;
  source_record_id: string;
  evidence_type: string;
  evidence_at: string | null;
  confidence: string;
  reason_code: string;
  source_email_id: number | null;
  source_attachment_id: number | null;
  detail_json: unknown;
  synced_at: string | null;
}

export interface CommercialOpportunityConflict {
  conflict_id: string;
  opportunity_id: string | null;
  conflict_type: string;
  reason_code: string;
  subject_keys_json: unknown;
  evidence_pointers_json: unknown;
  review_status: string;
  detail_json: unknown;
  synced_at: string | null;
}

export interface CommercialOpportunitiesResponse {
  meta: CommercialOpportunitiesMeta;
  items: CommercialOpportunityItem[];
}

export interface CommercialOpportunityDetailResponse {
  meta: {
    data_source: CommercialOpportunityDataSource;
    read_only: boolean;
  };
  opportunity: CommercialOpportunityItem;
  events: CommercialOpportunityEvent[];
  evidence: CommercialOpportunityEvidence[];
  conflicts: CommercialOpportunityConflict[];
}
