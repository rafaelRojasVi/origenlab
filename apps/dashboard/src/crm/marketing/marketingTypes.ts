/** Shapes of `/v2/workspace/marketing/*` and the two campaign-draft commands. */

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
