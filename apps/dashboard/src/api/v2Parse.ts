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
  V2CardMarketing,
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
    created_at: optionalStr(row.created_at),
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
export const parseV2OrganizationsPage = (value: unknown): V2Page<V2Organization> =>
  parsePage(value, parseV2Organization);
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

export function parseV2ContactCard(value: unknown): V2ContactCard {
  const row = asRecord(value);
  return {
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
