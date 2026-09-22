/**
 * UI types for the V2 durable read boundary (`/v2/*`).
 *
 * These describe the **durable** V2 core, not a rebuildable mirror. The distinction is the
 * point of the migration: a card fed from here is reading human commercial truth, while the
 * `mirror*` and `leadIntel*` clients beside it read machine projections that may be dropped
 * and rebuilt at any time.
 */

export interface V2Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** A channel, and only a channel. `usage` says what is actually known about its owner. */
export type V2ContactUsage =
  | "personal"
  | "work"
  | "shared_mailbox"
  | "individual_owner_unknown"
  | "unattributed";

/**
 * The relationship an operator may assert between an address and an institution.
 *
 * The other three usages need something the review queue does not have: `personal` and
 * `work` need a recorded person, and `unattributed` forbids the institution. These two are
 * what is left, and they say opposite things about who is on the other end — which is why
 * nothing in this app picks one for the operator.
 */
export type V2AttachableUsage = "shared_mailbox" | "individual_owner_unknown";

/** `machine_proposed` is the review queue by another name. */
export type V2Confirmation = "machine_proposed" | "confirmed";

export interface V2Contact {
  contact_point_id: string;
  address: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
  person_id: string | null;
  person_display_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
  created_at: string | null;
}

export interface V2Organization {
  organization_id: string;
  name: string;
  /** `unknown` until an operator classifies it; the closed vocabulary is still open. */
  kind: string;
  confirmation: V2Confirmation;
  parent_organization_id: string | null;
  contact_point_count: number;
  created_at: string | null;
}

export interface V2Opportunity {
  opportunity_id: string;
  title: string;
  stage: string;
  organization_id: string | null;
  organization_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface V2Task {
  task_id: string;
  title: string;
  due_at: string | null;
  overdue: boolean;
  opportunity_id: string | null;
  opportunity_title: string | null;
  organization_name: string | null;
}

export interface V2Quote {
  quote_id: string;
  quote_number: string | null;
  revision_no: number;
  status: string;
  sent_at: string | null;
  opportunity_id: string | null;
  organization_name: string | null;
  updated_at: string | null;
}

/**
 * The operator review queue, counted by why.
 *
 * `ambiguous_assertions` is what the migration stopped and asked about — a small,
 * actionable number. The `machine_proposed_*` counts are a much larger backlog of
 * transcribed observations awaiting confirmation. They are different decisions and are
 * deliberately not summed into one figure.
 */
export interface V2ReviewSummary {
  ambiguous_assertions: number;
  unresolved_assertions: number;
  machine_proposed_organizations: number;
  machine_proposed_contact_points: number;
  unattributed_contact_points: number;
}

// ---------------------------------------------------------------- the evidence trail

/** One observation and the record it came from. Provenance is never optional here. */
export interface V2EvidenceItem {
  assertion_id: string;
  kind: string;
  value_norm: string;
  resolution: string;
  resolved_kind: string | null;
  resolved_id: string | null;
  ambiguity_note: string | null;
  observed_at: string | null;
  source_record_id: string;
  source_kind: string;
  source_uri: string | null;
  source_review_status: string;
  source_is_quarantined: boolean;
  acquired_at: string | null;
}


// -------------------------------------------------------------- the review queue

/**
 * One observation a source record makes, as the record queue returns it.
 *
 * `resolution` is always `unresolved` for freshly staged evidence. Nothing in the dashboard
 * may change it: resolving an assertion is a durable decision and the V2 command boundary
 * that would record it does not exist yet.
 */
export interface V2RecordAssertion {
  assertion_id: string;
  kind: string;
  value_norm: string;
  resolution: string;
  resolved_kind: string | null;
  resolved_id: string | null;
  ambiguity_note: string | null;
}

/**
 * An asserted address that already exists as a `crm.contact_point`.
 *
 * It says the **address** exists. `person_id` is what says whether anyone is known to own
 * it, and it is usually null — the two must never be rendered as the same fact.
 */
export interface V2RecordContactMatch {
  value_norm: string;
  contact_point_id: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
  person_id: string | null;
  person_display_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
}

/** An asserted organization name that exactly equals an existing organization's name. */
export interface V2RecordOrganizationMatch {
  value_norm: string;
  organization_id: string;
  name: string;
  confirmation: V2Confirmation;
}

/** An organization reached through a *registered* domain — evidence, not resemblance. */
export interface V2RecordDomainOrganization {
  organization_id: string;
  name: string;
  scope: string | null;
}

export interface V2EvidenceRecord {
  source_record_id: string;
  source_kind: string;
  dedupe_key: string;
  source_uri: string | null;
  acquired_at: string | null;
  review_status: string;
  is_quarantined: boolean;
  subject: string | null;
  from_address: string | null;
  from_domain: string | null;
  message_date: string | null;
  thread_id: string | null;
  assertions: V2RecordAssertion[];
  /** The true number of assertions, which may exceed the capped list beside it. */
  assertion_total: number;
  contact_matches: V2RecordContactMatch[];
  organization_matches: V2RecordOrganizationMatch[];
  domain_organization: V2RecordDomainOrganization | null;
}

// ------------------------------------------------------------------------- the cards

/** An evidence row as it appears *on* a card — the same fact, without the paging keys. */
export interface V2CardEvidence {
  assertion_id: string;
  kind: string;
  value_norm: string;
  resolution: string;
  resolved_kind: string | null;
  ambiguity_note: string | null;
  observed_at: string | null;
  source_kind: string;
  source_uri: string | null;
  source_review_status: string;
  source_is_quarantined: boolean;
}

export interface V2ContactCardSibling {
  contact_point_id: string;
  address: string;
  channel_kind: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
}

export interface V2Affiliation {
  affiliation_id: string;
  organization_id: string;
  organization_name: string;
  role_title: string | null;
  unit_label: string | null;
  confirmation: V2Confirmation;
  valid_from: string | null;
  valid_to: string | null;
}

export interface V2CardMarketing {
  campaign_name: string;
  campaign_status: string;
  recipient_state: string;
  attempt_count: number;
  created_at: string | null;
}

/**
 * A control on an *address*, not on an identity.
 *
 * `outbound.contact_control` deliberately has no identity column: a suppression must keep
 * working for an address whose owner is unknown, was never promoted, or is later merged.
 * The card shows these beside the channel for exactly that reason.
 */
export interface V2AddressControl {
  control_kind: string;
  purpose: string;
  scope: string;
  reason: string | null;
  source: string;
  until_at: string | null;
  needs_review: boolean;
  created_at: string | null;
}

/** How many there really are, when a list is capped at the card's child limit. */
export type V2CardCounts = Record<string, number>;

export interface V2ContactCard {
  contact_point_id: string;
  channel_kind: string;
  address: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
  created_at: string | null;
  updated_at: string | null;
  person_id: string | null;
  person_display_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
  organization_kind: string | null;
  origin_source_kind: string | null;
  origin_source_uri: string | null;
  origin_review_status: string | null;
  sibling_contact_points: V2ContactCardSibling[];
  affiliations: V2Affiliation[];
  evidence: V2CardEvidence[];
  marketing: V2CardMarketing[];
  address_controls: V2AddressControl[];
  counts: V2CardCounts;
}

export interface V2OrganizationCardPerson {
  person_id: string;
  display_name: string;
  role_title: string | null;
  unit_label: string | null;
  confirmation: V2Confirmation;
  valid_from: string | null;
  valid_to: string | null;
}

export interface V2OrganizationDomain {
  organization_domain_id: string;
  domain: string;
  scope: string;
  created_at: string | null;
}

export interface V2OrganizationRelationship {
  organization_relationship_id: string;
  role: string;
  valid_from: string | null;
  valid_to: string | null;
  note: string | null;
}

export interface V2OrganizationCardChannel {
  contact_point_id: string;
  address: string;
  channel_kind: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
  person_display_name: string | null;
}

export interface V2OrganizationCardChild {
  organization_id: string;
  name: string;
  kind: string;
  confirmation: V2Confirmation;
}

export interface V2OrganizationCard {
  organization_id: string;
  name: string;
  legal_name: string | null;
  kind: string;
  confirmation: V2Confirmation;
  note: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  parent_organization_id: string | null;
  parent_organization_name: string | null;
  merged_into_organization_id: string | null;
  merged_into_organization_name: string | null;
  origin_source_kind: string | null;
  origin_source_uri: string | null;
  origin_review_status: string | null;
  contact_points: V2OrganizationCardChannel[];
  people: V2OrganizationCardPerson[];
  domains: V2OrganizationDomain[];
  relationships: V2OrganizationRelationship[];
  child_organizations: V2OrganizationCardChild[];
  evidence: V2CardEvidence[];
  counts: V2CardCounts;
}
