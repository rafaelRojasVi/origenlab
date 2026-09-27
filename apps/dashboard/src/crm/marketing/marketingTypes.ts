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
  content_sha256?: string | null;
  audience_sha256?: string | null;
  audience_policy_version?: string | null;
  audience_frozen_at?: string | null;
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

// ── audience freeze ─────────────────────────────────────────────────────────────────────────

export interface FreezeCriteria {
  family_id: string;
  brand_id: string;
  model_id: string;
  organization_id: string;
  bases: InterestBasis[];
  recorded: "" | "crm" | "evidence";
  q: string;
  scope: "persons" | "institutions" | "both";
}

export interface CodeLabel {
  code: string;
  label: string;
}

export interface FreezeEvidence {
  level: "person" | "institution";
  organization_id: string | null;
  brand_id: string;
  family_id: string;
  model_id: string | null;
  basis: InterestBasis;
  source_kind: string;
  opportunity_id: string | null;
  source_record_id: string | null;
  interest_id: string | null;
  recorded_in_crm: boolean;
  observed_at: string | null;
}

export interface FreezeLine {
  brand_id: string;
  line: string;
  brand: string;
  status: "evidenced" | "sin_informacion";
  label: string;
  bases: InterestBasis[];
  latest_observed_at: string | null;
}

export interface FreezeRow {
  key: string;
  address: string;
  display_name: string | null;
  organizations: { organization_id: string; name: string | null }[];
  via: string[];
  evidence: FreezeEvidence[];
  lines: FreezeLine[];
  relevance: "evidenced" | "sin_informacion";
  evidence_observed_at: string | null;
  review_codes: string[];
  inclusion: "included" | "excluded";
  reasons: CodeLabel[];
  note_labels: CodeLabel[];
}

export interface SendBlocker {
  code: string;
  label: string;
  detail: string;
}

export interface FreezeProblem {
  code: string;
  message: string;
}

export interface FreezePreview {
  campaign_id: string;
  policy_version: string;
  criteria: Record<string, unknown>;
  preview_sha256: string;
  content: {
    subject: string | null;
    preheader: string | null;
    has_html: boolean;
    body_text_chars: number;
    content_sha256: string | null;
    campaign_version: number;
    promises_baja: boolean;
  };
  rows: FreezeRow[];
  review_required: string[];
  review_pending: string[];
  malformed_count: number;
  counts: {
    candidates: number;
    rows: number;
    included: number;
    excluded: number;
    malformed_not_stored: number;
    review_required: number;
    excluded_by_reason: (CodeLabel & { count: number })[];
    included_by_line: { brand_id: string; line: string; brand: string; evidenced: number; sin_informacion: number }[];
  };
  problems: FreezeProblem[];
  send_blockers: SendBlocker[];
  freeze_enabled: boolean;
}

export interface ReviewDecision {
  key: string;
  decision: "include" | "exclude";
  note: string;
}

export interface FreezeResult {
  campaign_id: string;
  status: "audience_frozen";
  version: number;
  frozen_at: string;
  policy_version: string;
  content_sha256: string;
  audience_sha256: string;
  counts: { rows: number; included: number; excluded: number; malformed_not_stored: number };
  storage: { tables: string[]; database: string };
  send_blockers: SendBlocker[];
  replayed: boolean;
}

export interface FrozenRecipient {
  recipient_id: string;
  address: string;
  organization_name: string | null;
  display_name: string | null;
  frozen_at: string;
  inclusion: "included" | "excluded";
  frozen_reasons: string[];
  frozen_notes: string[];
  relevance: "evidenced" | "sin_informacion";
  interest_evidence: FreezeEvidence[];
  evidence_observed_at: string | null;
  identity_review: { codes: string[]; decision: string; note: string } | null;
  campaign_version: number;
  content_sha256: string;
  policy_version: string;
  lifecycle_state: string;
}

export interface FrozenSnapshot {
  campaign_id: string;
  name: string;
  status: string;
  version: number;
  audience_frozen_at: string | null;
  audience_policy_version: string | null;
  content_sha256: string | null;
  audience_sha256: string | null;
  recipients: FrozenRecipient[];
  storage: { table: string; database: string };
  send_blockers: SendBlocker[];
}
