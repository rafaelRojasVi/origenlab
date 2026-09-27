/**
 * Parse V2 durable read-boundary responses.
 *
 * Total-only responses are parsed as totals. Card counts come from `total`, never from
 * `items.length`: the boundary pages at 200, so counting the array would silently cap any
 * card at 200 and quietly understate the operator's workload.
 */

import type {
  V2AddressControl,
  V2Affiliation,
  V2CardCounts,
  V2CardEvidence,
  V2CaseEvidence,
  V2CaseEvidenceRelation,
  V2CaseInterest,
  V2CaseOrganization,
  V2CaseOrganizationRole,
  V2CaseStage,
  V2CaseStageMachine,
  V2CommercialCase,
  V2CommercialCaseCard,
  V2CardConnections,
  V2CardMarketing,
  V2CaseParticipant,
  V2ConnectedActivity,
  V2ConnectedCase,
  V2ConnectedCaseEvidence,
  V2ConnectedInterest,
  V2ConnectedQuote,
  V2Contact,
  V2ContactCard,
  V2ContactCardSibling,
  V2EvidenceItem,
  V2EvidenceRecord,
  V2RecordAssertion,
  V2RecordContactMatch,
  V2RecordDomainOrganization,
  V2RecordOrganizationMatch,
  V2OrganizationCard,
  V2OrganizationCardChannel,
  V2OrganizationCardChild,
  V2OrganizationCardPerson,
  V2OrganizationDomain,
  V2OrganizationRelationship,
  V2ContactUsage,
  V2Confirmation,
  V2Opportunity,
  V2Organization,
  V2OrganizationFacets,
  V2OrganizationsPage,
  V2OrganizationCase,
  V2OrganizationCaseRole,
  V2Page,
  V2Quote,
  V2ReviewSummary,
  V2Task,
} from "./v2Types";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalStr(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function int(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

const USAGES: ReadonlySet<string> = new Set([
  "personal",
  "work",
  "shared_mailbox",
  "individual_owner_unknown",
  "unattributed",
]);

function usage(value: unknown): V2ContactUsage {
  const raw = str(value);
  // An unrecognised usage becomes `unattributed` rather than being passed through. The UI
  // must never claim more about a channel's owner than the vocabulary it understands.
  return (USAGES.has(raw) ? raw : "unattributed") as V2ContactUsage;
}

function confirmation(value: unknown): V2Confirmation {
  return str(value) === "confirmed" ? "confirmed" : "machine_proposed";
}

function parsePage<T>(value: unknown, parseItem: (row: unknown) => T): V2Page<T> {
  const record = asRecord(value);
  const rawItems = Array.isArray(record.items) ? record.items : [];
  return {
    items: rawItems.map(parseItem),
    total: int(record.total),
    limit: int(record.limit),
    offset: int(record.offset),
  };
}

export function parseV2Contact(value: unknown): V2Contact {
  const row = asRecord(value);
  return {
    contact_point_id: str(row.contact_point_id),
    address: str(row.address),
    usage: usage(row.usage),
    confirmation: confirmation(row.confirmation),
    person_id: optionalStr(row.person_id),
    person_display_name: optionalStr(row.person_display_name),
    organization_id: optionalStr(row.organization_id),
    organization_name: optionalStr(row.organization_name),
    created_at: optionalStr(row.created_at),
    case_count: int(row.case_count),
    address_control_count: int(row.address_control_count),
  };
}

export function parseV2Organization(value: unknown): V2Organization {
  const row = asRecord(value);
  return {
    organization_id: str(row.organization_id),
    name: str(row.name),
    kind: str(row.kind) || "unknown",
    confirmation: confirmation(row.confirmation),
    parent_organization_id: optionalStr(row.parent_organization_id),
    contact_point_count: int(row.contact_point_count),
    confirmed_people_count: int(row.confirmed_people_count),
    case_count: int(row.case_count),
    open_case_count: int(row.open_case_count),
    interest_count: int(row.interest_count),
    quote_count: int(row.quote_count),
    activity_count: int(row.activity_count),
    last_activity_at: optionalStr(row.last_activity_at),
    cases_as_requesting_institution: int(row.cases_as_requesting_institution),
    cases_as_end_user_institution: int(row.cases_as_end_user_institution),
    cases_as_purchasing_agent: int(row.cases_as_purchasing_agent),
    cases_as_funder: int(row.cases_as_funder),
    cases_as_supplier: int(row.cases_as_supplier),
    cases_as_manufacturer: int(row.cases_as_manufacturer),
    cases_as_mentioned: int(row.cases_as_mentioned),
    relationship_roles: Array.isArray(row.relationship_roles)
      ? row.relationship_roles.map((role) => str(role)).filter((role) => role !== "")
      : [],
    created_at: optionalStr(row.created_at),
  };
}

function parseOrganizationFacets(value: unknown): V2OrganizationFacets | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = asRecord(value);
  return {
    all: int(row.all),
    customers: int(row.customers),
    suppliers: int(row.suppliers),
    others: int(row.others),
  };
}

export function parseV2Opportunity(value: unknown): V2Opportunity {
  const row = asRecord(value);
  return {
    opportunity_id: str(row.opportunity_id),
    title: str(row.title),
    stage: str(row.stage),
    organization_id: optionalStr(row.organization_id),
    organization_name: optionalStr(row.organization_name),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
  };
}

export function parseV2Task(value: unknown): V2Task {
  const row = asRecord(value);
  return {
    task_id: str(row.task_id),
    title: str(row.title),
    due_at: optionalStr(row.due_at),
    overdue: bool(row.overdue),
    opportunity_id: optionalStr(row.opportunity_id),
    opportunity_title: optionalStr(row.opportunity_title),
    organization_name: optionalStr(row.organization_name),
  };
}

export function parseV2Quote(value: unknown): V2Quote {
  const row = asRecord(value);
  return {
    quote_id: str(row.quote_id),
    quote_number: optionalStr(row.quote_number),
    revision_no: int(row.revision_no),
    status: str(row.status),
    sent_at: optionalStr(row.sent_at),
    opportunity_id: optionalStr(row.opportunity_id),
    organization_name: optionalStr(row.organization_name),
    updated_at: optionalStr(row.updated_at),
  };
}

export function parseV2ReviewSummary(value: unknown): V2ReviewSummary {
  const row = asRecord(value);
  return {
    ambiguous_assertions: int(row.ambiguous_assertions),
    unresolved_assertions: int(row.unresolved_assertions),
    machine_proposed_organizations: int(row.machine_proposed_organizations),
    machine_proposed_contact_points: int(row.machine_proposed_contact_points),
    unattributed_contact_points: int(row.unattributed_contact_points),
  };
}

export const parseV2ContactsPage = (value: unknown): V2Page<V2Contact> =>
  parsePage(value, parseV2Contact);
export const parseV2OrganizationsPage = (value: unknown): V2OrganizationsPage => ({
  ...parsePage(value, parseV2Organization),
  facets: parseOrganizationFacets(asRecord(value).facets),
});
export const parseV2OpportunitiesPage = (value: unknown): V2Page<V2Opportunity> =>
  parsePage(value, parseV2Opportunity);
export const parseV2TasksPage = (value: unknown): V2Page<V2Task> =>
  parsePage(value, parseV2Task);
export const parseV2QuotesPage = (value: unknown): V2Page<V2Quote> =>
  parsePage(value, parseV2Quote);

// ---------------------------------------------------------------- evidence and cards

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/**
 * Counts are read from the response, never derived from the array beside them.
 *
 * Every child list on a card is capped server-side, so `items.length` is a floor and the
 * count is the truth. A UI that counted the array would understate a large organization
 * and show a confident, wrong number.
 */
function counts(value: unknown): V2CardCounts {
  const row = asRecord(value);
  const out: V2CardCounts = {};
  for (const [key, raw] of Object.entries(row)) {
    out[key] = int(raw);
  }
  return out;
}

export function parseV2EvidenceItem(value: unknown): V2EvidenceItem {
  const row = asRecord(value);
  return {
    assertion_id: str(row.assertion_id),
    kind: str(row.kind),
    value_norm: str(row.value_norm),
    resolution: str(row.resolution),
    resolved_kind: optionalStr(row.resolved_kind),
    resolved_id: optionalStr(row.resolved_id),
    ambiguity_note: optionalStr(row.ambiguity_note),
    observed_at: optionalStr(row.observed_at),
    source_record_id: str(row.source_record_id),
    source_kind: str(row.source_kind),
    source_uri: optionalStr(row.source_uri),
    source_review_status: str(row.source_review_status),
    source_is_quarantined: bool(row.source_is_quarantined),
  acquired_at: optionalStr(row.acquired_at),
  };
}

function parseCardEvidence(value: unknown): V2CardEvidence {
  const row = asRecord(value);
  return {
    assertion_id: str(row.assertion_id),
    kind: str(row.kind),
    value_norm: str(row.value_norm),
    resolution: str(row.resolution),
    resolved_kind: optionalStr(row.resolved_kind),
    ambiguity_note: optionalStr(row.ambiguity_note),
    observed_at: optionalStr(row.observed_at),
    source_kind: str(row.source_kind),
    source_uri: optionalStr(row.source_uri),
    source_review_status: str(row.source_review_status),
    source_is_quarantined: bool(row.source_is_quarantined),
  };
}

function parseSibling(value: unknown): V2ContactCardSibling {
  const row = asRecord(value);
  return {
    contact_point_id: str(row.contact_point_id),
    address: str(row.address),
    channel_kind: str(row.channel_kind),
    usage: usage(row.usage),
    confirmation: confirmation(row.confirmation),
  };
}

function parseAffiliation(value: unknown): V2Affiliation {
  const row = asRecord(value);
  return {
    affiliation_id: str(row.affiliation_id),
    organization_id: str(row.organization_id),
    organization_name: str(row.organization_name),
    role_title: optionalStr(row.role_title),
    unit_label: optionalStr(row.unit_label),
    confirmation: confirmation(row.confirmation),
    valid_from: optionalStr(row.valid_from),
    valid_to: optionalStr(row.valid_to),
  };
}

function parseMarketing(value: unknown): V2CardMarketing {
  const row = asRecord(value);
  return {
    contact_point_id: optionalStr(row.contact_point_id),
    address: optionalStr(row.address),
    campaign_name: str(row.campaign_name),
    campaign_status: str(row.campaign_status),
    recipient_state: str(row.recipient_state),
    attempt_count: int(row.attempt_count),
    created_at: optionalStr(row.created_at),
  };
}

function parseAddressControl(value: unknown): V2AddressControl {
  const row = asRecord(value);
  return {
    value_norm: optionalStr(row.value_norm),
    control_kind: str(row.control_kind),
    purpose: str(row.purpose),
    scope: str(row.scope),
    reason: optionalStr(row.reason),
    source: str(row.source),
    until_at: optionalStr(row.until_at),
    needs_review: bool(row.needs_review),
    created_at: optionalStr(row.created_at),
  };
}

function parseConnectedCase(value: unknown): V2ConnectedCase {
  const row = asRecord(value);
  return {
    opportunity_id: str(row.opportunity_id),
    title: str(row.title),
    stage: caseStage(row.stage),
    closed_at: optionalStr(row.closed_at),
    close_reason: optionalStr(row.close_reason),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
    requesting_organization_id: optionalStr(row.requesting_organization_id),
    requesting_organization_name: optionalStr(row.requesting_organization_name),
    roles: list(row.roles).map(str).filter((role) => role.length > 0),
  };
}

function parseConnectedInterest(value: unknown): V2ConnectedInterest {
  const row = asRecord(value);
  return {
    opportunity_interest_id: str(row.opportunity_interest_id),
    opportunity_id: str(row.opportunity_id),
    opportunity_title: str(row.opportunity_title),
    product_id: optionalStr(row.product_id),
    product_model_number: optionalStr(row.product_model_number),
    product_name: optionalStr(row.product_name),
    manufacturer_organization_id: optionalStr(row.manufacturer_organization_id),
    manufacturer_organization_name: optionalStr(row.manufacturer_organization_name),
    model_text: optionalStr(row.model_text),
    description: optionalStr(row.description),
    quantity: optionalNumber(row.quantity),
    quantity_unit: optionalStr(row.quantity_unit),
    confirmation: confirmation(row.confirmation),
    created_at: optionalStr(row.created_at),
  };
}

function parseConnectedQuote(value: unknown): V2ConnectedQuote {
  const row = asRecord(value);
  return {
    quote_id: str(row.quote_id),
    quote_number: optionalStr(row.quote_number),
    opportunity_id: str(row.opportunity_id),
    opportunity_title: str(row.opportunity_title),
    latest_revision_no: optionalNumber(row.latest_revision_no),
    latest_status: optionalStr(row.latest_status),
    quote_currency: optionalStr(row.quote_currency),
    grand_total: optionalNumber(row.grand_total),
    valid_until: optionalStr(row.valid_until),
    sent_at: optionalStr(row.sent_at),
    revision_count: int(row.revision_count),
    updated_at: optionalStr(row.updated_at),
  };
}

function parseConnectedActivity(value: unknown): V2ConnectedActivity {
  const row = asRecord(value);
  return {
    activity_id: str(row.activity_id),
    opportunity_id: str(row.opportunity_id),
    opportunity_title: str(row.opportunity_title),
    kind: str(row.kind),
    occurred_at: optionalStr(row.occurred_at),
    summary: optionalStr(row.summary),
    recorded_by_display_name: optionalStr(row.recorded_by_display_name),
  };
}

function parseConnectedCaseEvidence(value: unknown): V2ConnectedCaseEvidence {
  const row = asRecord(value);
  return {
    opportunity_evidence_id: str(row.opportunity_evidence_id),
    opportunity_id: str(row.opportunity_id),
    relation: caseRelation(row.relation),
    subject_kind: caseSubjectKind(row.subject_kind),
    subject_id: str(row.subject_id),
    source_kind: optionalStr(row.source_kind),
    source_uri: optionalStr(row.source_uri),
    linked_at: optionalStr(row.linked_at),
  };
}

/** The case connections both cards carry, spread flat on the payload by the API. */
function parseCardConnections(row: Record<string, unknown>): V2CardConnections {
  const summary = asRecord(row.connection_summary);
  return {
    cases: list(row.cases).map(parseConnectedCase),
    interests: list(row.interests).map(parseConnectedInterest),
    quotes: list(row.quotes).map(parseConnectedQuote),
    activities: list(row.activities).map(parseConnectedActivity),
    case_evidence: list(row.case_evidence).map(parseConnectedCaseEvidence),
    connection_summary: {
      cases: int(summary.cases),
      open_cases: int(summary.open_cases),
      interests: int(summary.interests),
      quotes: int(summary.quotes),
      activities: int(summary.activities),
      case_evidence: int(summary.case_evidence),
      last_activity_at: optionalStr(summary.last_activity_at),
      confirmed_people:
        typeof summary.confirmed_people === "number" ? int(summary.confirmed_people) : null,
    },
  };
}

export function parseV2ContactCard(value: unknown): V2ContactCard {
  const row = asRecord(value);
  return {
    ...parseCardConnections(row),
    contact_point_id: str(row.contact_point_id),
    channel_kind: str(row.channel_kind),
    address: str(row.address),
    usage: usage(row.usage),
    confirmation: confirmation(row.confirmation),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
    person_id: optionalStr(row.person_id),
    person_display_name: optionalStr(row.person_display_name),
    organization_id: optionalStr(row.organization_id),
    organization_name: optionalStr(row.organization_name),
    organization_kind: optionalStr(row.organization_kind),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    origin_review_status: optionalStr(row.origin_review_status),
    sibling_contact_points: list(row.sibling_contact_points).map(parseSibling),
    affiliations: list(row.affiliations).map(parseAffiliation),
    evidence: list(row.evidence).map(parseCardEvidence),
    marketing: list(row.marketing).map(parseMarketing),
    address_controls: list(row.address_controls).map(parseAddressControl),
    counts: counts(row.counts),
  };
}

function parseOrgChannel(value: unknown): V2OrganizationCardChannel {
  const row = asRecord(value);
  return {
    contact_point_id: str(row.contact_point_id),
    address: str(row.address),
    channel_kind: str(row.channel_kind),
    usage: usage(row.usage),
    confirmation: confirmation(row.confirmation),
    person_display_name: optionalStr(row.person_display_name),
  };
}

function parseOrgPerson(value: unknown): V2OrganizationCardPerson {
  const row = asRecord(value);
  return {
    person_id: str(row.person_id),
    display_name: str(row.display_name),
    role_title: optionalStr(row.role_title),
    unit_label: optionalStr(row.unit_label),
    confirmation: confirmation(row.confirmation),
    valid_from: optionalStr(row.valid_from),
    valid_to: optionalStr(row.valid_to),
  };
}

function parseOrgDomain(value: unknown): V2OrganizationDomain {
  const row = asRecord(value);
  return {
    organization_domain_id: str(row.organization_domain_id),
    domain: str(row.domain),
    scope: str(row.scope),
    created_at: optionalStr(row.created_at),
  };
}

function parseOrgRelationship(value: unknown): V2OrganizationRelationship {
  const row = asRecord(value);
  return {
    organization_relationship_id: str(row.organization_relationship_id),
    role: str(row.role),
    valid_from: optionalStr(row.valid_from),
    valid_to: optionalStr(row.valid_to),
    note: optionalStr(row.note),
  };
}

function parseOrgChild(value: unknown): V2OrganizationCardChild {
  const row = asRecord(value);
  return {
    organization_id: str(row.organization_id),
    name: str(row.name),
    kind: str(row.kind),
    confirmation: confirmation(row.confirmation),
  };
}

export function parseV2OrganizationCard(value: unknown): V2OrganizationCard {
  const row = asRecord(value);
  return {
    ...parseCardConnections(row),
    organization_id: str(row.organization_id),
    name: str(row.name),
    legal_name: optionalStr(row.legal_name),
    kind: str(row.kind),
    confirmation: confirmation(row.confirmation),
    note: optionalStr(row.note),
    version: int(row.version),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
    parent_organization_id: optionalStr(row.parent_organization_id),
    parent_organization_name: optionalStr(row.parent_organization_name),
    merged_into_organization_id: optionalStr(row.merged_into_organization_id),
    merged_into_organization_name: optionalStr(row.merged_into_organization_name),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    origin_review_status: optionalStr(row.origin_review_status),
    contact_points: list(row.contact_points).map(parseOrgChannel),
    people: list(row.people).map(parseOrgPerson),
    domains: list(row.domains).map(parseOrgDomain),
    relationships: list(row.relationships).map(parseOrgRelationship),
    child_organizations: list(row.child_organizations).map(parseOrgChild),
    evidence: list(row.evidence).map(parseCardEvidence),
    address_controls: list(row.address_controls).map(parseAddressControl),
    domain_controls: list(row.domain_controls).map(parseAddressControl),
    marketing: list(row.marketing).map(parseMarketing),
    counts: counts(row.counts),
  };
}

export const parseV2EvidencePage = (value: unknown): V2Page<V2EvidenceItem> =>
  parsePage(value, parseV2EvidenceItem);

// -------------------------------------------------------------- the review queue

function parseRecordAssertion(value: unknown): V2RecordAssertion {
  const row = asRecord(value);
  return {
    assertion_id: str(row.assertion_id),
    kind: str(row.kind),
    value_norm: str(row.value_norm),
    resolution: str(row.resolution),
    resolved_kind: optionalStr(row.resolved_kind),
    resolved_id: optionalStr(row.resolved_id),
    ambiguity_note: optionalStr(row.ambiguity_note),
  };
}

function parseRecordContactMatch(value: unknown): V2RecordContactMatch {
  const row = asRecord(value);
  return {
    value_norm: str(row.value_norm),
    contact_point_id: str(row.contact_point_id),
    usage: usage(row.usage),
    confirmation: confirmation(row.confirmation),
    person_id: optionalStr(row.person_id),
    person_display_name: optionalStr(row.person_display_name),
    organization_id: optionalStr(row.organization_id),
    organization_name: optionalStr(row.organization_name),
  };
}

function parseRecordOrganizationMatch(value: unknown): V2RecordOrganizationMatch {
  const row = asRecord(value);
  return {
    value_norm: str(row.value_norm),
    organization_id: str(row.organization_id),
    name: str(row.name),
    confirmation: confirmation(row.confirmation),
  };
}

function parseDomainOrganization(value: unknown): V2RecordDomainOrganization | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = asRecord(value);
  const organizationId = str(row.organization_id);
  // A domain match with no organization id is not a weaker match, it is a malformed one.
  // Dropping it keeps "this domain is registered to an organization" an all-or-nothing
  // claim rather than something the UI half-renders.
  return organizationId
    ? { organization_id: organizationId, name: str(row.name), scope: optionalStr(row.scope) }
    : null;
}

export function parseV2EvidenceRecord(value: unknown): V2EvidenceRecord {
  const row = asRecord(value);
  return {
    source_record_id: str(row.source_record_id),
    source_kind: str(row.source_kind),
    dedupe_key: str(row.dedupe_key),
    source_uri: optionalStr(row.source_uri),
    acquired_at: optionalStr(row.acquired_at),
    review_status: str(row.review_status),
    is_quarantined: bool(row.is_quarantined),
    subject: optionalStr(row.subject),
    from_address: optionalStr(row.from_address),
    from_domain: optionalStr(row.from_domain),
    message_date: optionalStr(row.message_date),
    thread_id: optionalStr(row.thread_id),
    assertions: (Array.isArray(row.assertions) ? row.assertions : []).map(parseRecordAssertion),
    assertion_total: int(row.assertion_total),
    contact_matches: (Array.isArray(row.contact_matches) ? row.contact_matches : []).map(
      parseRecordContactMatch,
    ),
    organization_matches: (
      Array.isArray(row.organization_matches) ? row.organization_matches : []
    ).map(parseRecordOrganizationMatch),
    domain_organization: parseDomainOrganization(row.domain_organization),
  };
}

export function parseV2EvidenceRecordsPage(value: unknown): V2Page<V2EvidenceRecord> {
  return parsePage(value, parseV2EvidenceRecord);
}

// ------------------------------------------------------------------ commercial cases

const CASE_STAGES: ReadonlySet<string> = new Set([
  "lead",
  "qualifying",
  "qualified",
  "quoting",
  "negotiating",
  "won",
  "lost",
  "abandoned",
]);

/**
 * An unrecognised stage becomes `lead`, the opening stage, rather than being passed through.
 *
 * Every other choice is worse. Passing it through would let an unknown word reach a label
 * lookup and render as itself, and picking a terminal stage would have the UI claim a case
 * is closed on the strength of a string it did not understand. `lead` claims the least.
 */
function caseStage(value: unknown): V2CaseStage {
  const raw = str(value);
  return (CASE_STAGES.has(raw) ? raw : "lead") as V2CaseStage;
}

function caseStages(value: unknown): V2CaseStage[] {
  return list(value)
    .map((entry) => str(entry))
    .filter((entry) => CASE_STAGES.has(entry)) as V2CaseStage[];
}

const CASE_ROLES: ReadonlySet<string> = new Set([
  "requesting_institution",
  "end_user_institution",
  "purchasing_agent",
  "funder",
  "supplier",
  "manufacturer",
  "mentioned",
]);

/**
 * An unrecognised part becomes `mentioned`.
 *
 * `mentioned` is the one part that asserts nothing commercial about an institution, so it
 * is the only safe landing place for a value this build does not know. Falling back to
 * `requesting_institution` would have the UI say who is buying on the strength of a typo.
 */
function caseRole(value: unknown): V2CaseOrganizationRole {
  const raw = str(value);
  return (CASE_ROLES.has(raw) ? raw : "mentioned") as V2CaseOrganizationRole;
}

const CASE_RELATIONS: ReadonlySet<string> = new Set([
  "origin",
  "supports_requesting_institution",
  "supports_interest",
  "supports_participant",
  "mentions",
  "contradicts",
]);

/** An unrecognised reading becomes `mentions`, which is the weakest claim in the list. */
function caseRelation(value: unknown): V2CaseEvidenceRelation {
  const raw = str(value);
  return (CASE_RELATIONS.has(raw) ? raw : "mentions") as V2CaseEvidenceRelation;
}

const CASE_SUBJECT_KINDS: ReadonlySet<string> = new Set([
  "source_record",
  "assertion",
  "message",
  "notice",
]);

function caseSubjectKind(value: unknown): V2CaseEvidence["subject_kind"] {
  const raw = str(value);
  return (
    CASE_SUBJECT_KINDS.has(raw) ? raw : "source_record"
  ) as V2CaseEvidence["subject_kind"];
}

/** A nullable confirmation: `null` is the answer when there is no row to be confirmed. */
function optionalConfirmation(value: unknown): V2Confirmation | null {
  const raw = str(value);
  if (!raw) {
    return null;
  }
  return raw === "confirmed" ? "confirmed" : "machine_proposed";
}

/**
 * `crm.opportunity_interest.quantity` is `numeric(18,3)`, which JSON carries as a number or
 * a string depending on the driver. Both are accepted; anything unparseable is null, which
 * renders as "no quantity" rather than as a confident zero.
 */
function optionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function parseV2CommercialCase(value: unknown): V2CommercialCase {
  const row = asRecord(value);
  return {
    opportunity_id: str(row.opportunity_id),
    title: str(row.title),
    stage: caseStage(row.stage),
    version: int(row.version),
    closed_at: optionalStr(row.closed_at),
    close_reason: optionalStr(row.close_reason),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
    owner_operator_id: str(row.owner_operator_id),
    owner_display_name: str(row.owner_display_name),
    origin_source_record_id: optionalStr(row.origin_source_record_id),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    requesting_organization_id: optionalStr(row.requesting_organization_id),
    requesting_organization_name: optionalStr(row.requesting_organization_name),
    requesting_confirmation: optionalConfirmation(row.requesting_confirmation),
    organization_count: int(row.organization_count),
    interest_count: int(row.interest_count),
    evidence_count: int(row.evidence_count),
    // Absent (not empty) on routes that do not carry them: null, never a claimed zero.
    participants: Array.isArray(row.participants)
      ? row.participants.map(parseCaseParticipant)
      : null,
    interest_labels: Array.isArray(row.interest_labels)
      ? row.interest_labels.map(str).filter((label) => label.length > 0)
      : null,
    quote_count: typeof row.quote_count === "number" ? int(row.quote_count) : null,
    latest_quote_status: optionalStr(row.latest_quote_status),
    last_activity_at: optionalStr(row.last_activity_at),
  };
}

function parseCaseParticipant(value: unknown): V2CaseParticipant {
  const row = asRecord(value);
  return {
    organization_id: str(row.organization_id),
    name: str(row.name),
    role: caseRole(row.role),
    confirmation: confirmation(row.confirmation),
  };
}

function parseCaseOrganization(value: unknown): V2CaseOrganization {
  const row = asRecord(value);
  return {
    opportunity_organization_id: str(row.opportunity_organization_id),
    organization_id: str(row.organization_id),
    name: str(row.name),
    organization_kind: str(row.organization_kind) || "unknown",
    role: caseRole(row.role),
    confirmation: confirmation(row.confirmation),
    valid_from: optionalStr(row.valid_from),
    valid_to: optionalStr(row.valid_to),
    is_current: bool(row.is_current),
    confirmed_by_display_name: optionalStr(row.confirmed_by_display_name),
    note: optionalStr(row.note),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    supplier_exception_reason: optionalStr(row.supplier_exception_reason),
    supplier_exception_at: optionalStr(row.supplier_exception_at),
    supplier_exception_by_display_name: optionalStr(row.supplier_exception_by_display_name),
  };
}

function parseCaseInterest(value: unknown): V2CaseInterest {
  const row = asRecord(value);
  return {
    opportunity_interest_id: str(row.opportunity_interest_id),
    product_id: optionalStr(row.product_id),
    product_name: optionalStr(row.product_name),
    manufacturer_organization_id: optionalStr(row.manufacturer_organization_id),
    manufacturer_organization_name: optionalStr(row.manufacturer_organization_name),
    model_text: optionalStr(row.model_text),
    description: optionalStr(row.description),
    quantity: optionalNumber(row.quantity),
    quantity_unit: optionalStr(row.quantity_unit),
    confirmation: confirmation(row.confirmation),
    confirmed_by_display_name: optionalStr(row.confirmed_by_display_name),
    withdrawn_at: optionalStr(row.withdrawn_at),
    withdraw_reason: optionalStr(row.withdraw_reason),
    note: optionalStr(row.note),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    created_at: optionalStr(row.created_at),
  };
}

function parseCaseEvidence(value: unknown): V2CaseEvidence {
  const row = asRecord(value);
  return {
    opportunity_evidence_id: str(row.opportunity_evidence_id),
    relation: caseRelation(row.relation),
    subject_kind: caseSubjectKind(row.subject_kind),
    subject_id: str(row.subject_id),
    source_kind: optionalStr(row.source_kind),
    source_uri: optionalStr(row.source_uri),
    source_review_status: optionalStr(row.source_review_status),
    assertion_kind: optionalStr(row.assertion_kind),
    assertion_value: optionalStr(row.assertion_value),
    linked_by_display_name: str(row.linked_by_display_name),
    linked_at: optionalStr(row.linked_at),
    unlinked_at: optionalStr(row.unlinked_at),
    unlink_reason: optionalStr(row.unlink_reason),
    note: optionalStr(row.note),
  };
}

/**
 * The stage machine as the API served it.
 *
 * `allowed_next_stages` is left exactly as it arrived, minus anything this build does not
 * recognise. It is never computed here and never defaulted to a non-empty list: a response
 * that carries no machine leaves a terminal-looking case with no moves, which is the safe
 * reading when the surface offers no move anyway.
 */
function parseCaseStageMachine(value: unknown, stage: V2CaseStage): V2CaseStageMachine {
  const row = asRecord(value);
  return {
    stage: row.stage === undefined ? stage : caseStage(row.stage),
    allowed_next_stages: caseStages(row.allowed_next_stages),
    is_terminal: bool(row.is_terminal),
    stages_requiring_a_requesting_institution: caseStages(
      row.stages_requiring_a_requesting_institution,
    ),
    stages_requiring_a_close_reason: caseStages(row.stages_requiring_a_close_reason),
  };
}

export function parseV2CommercialCaseCard(value: unknown): V2CommercialCaseCard {
  const row = asRecord(value);
  const stage = caseStage(row.stage);
  return {
    opportunity_id: str(row.opportunity_id),
    title: str(row.title),
    stage,
    version: int(row.version),
    closed_at: optionalStr(row.closed_at),
    close_reason: optionalStr(row.close_reason),
    created_at: optionalStr(row.created_at),
    updated_at: optionalStr(row.updated_at),
    owner_operator_id: str(row.owner_operator_id),
    owner_display_name: str(row.owner_display_name),
    organization_id: optionalStr(row.organization_id),
    organization_name: optionalStr(row.organization_name),
    origin_source_record_id: optionalStr(row.origin_source_record_id),
    origin_source_kind: optionalStr(row.origin_source_kind),
    origin_source_uri: optionalStr(row.origin_source_uri),
    origin_review_status: optionalStr(row.origin_review_status),
    reopened_from_opportunity_id: optionalStr(row.reopened_from_opportunity_id),
    reopened_from_title: optionalStr(row.reopened_from_title),
    organizations: list(row.organizations).map(parseCaseOrganization),
    interests: list(row.interests).map(parseCaseInterest),
    evidence: list(row.evidence).map(parseCaseEvidence),
    stage_machine: parseCaseStageMachine(row.stage_machine, stage),
    counts: counts(row.counts),
  };
}

export const parseV2CasesPage = (value: unknown): V2Page<V2CommercialCase> =>
  parsePage(value, parseV2CommercialCase);

function parseOrganizationCaseRole(value: unknown): V2OrganizationCaseRole {
  const row = asRecord(value);
  return {
    opportunity_organization_id: str(row.opportunity_organization_id),
    role: caseRole(row.role),
    confirmation: confirmation(row.confirmation),
    valid_from: optionalStr(row.valid_from),
    valid_to: optionalStr(row.valid_to),
    /*
      Derived from `valid_to` rather than trusted from the payload. The server sends both
      and they agree, but if they ever did not, the date is the fact and the boolean is a
      convenience — and a part shown as current when its row has closed is the one error
      this screen must not make.
    */
    is_current: optionalStr(row.valid_to) === null,
    note: optionalStr(row.note),
    supplier_exception_reason: optionalStr(row.supplier_exception_reason),
  };
}

export function parseV2OrganizationCase(value: unknown): V2OrganizationCase {
  const row = asRecord(value);
  return {
    ...parseV2CommercialCase(row),
    roles: list(row.roles).map(parseOrganizationCaseRole),
  };
}

export const parseV2OrganizationCasesPage = (
  value: unknown,
): V2Page<V2OrganizationCase> => parsePage(value, parseV2OrganizationCase);
