/**
 * Invented contacts and institutions, for tests.
 *
 * Every address here is under `.invalid`, which is reserved by RFC 2606 and can never be
 * delivered to. This repository is public and the real rows these shapes describe are real
 * people's mailboxes; a fixture is not a reason to put one in Git.
 */

import type {
  V2AddressControl,
  V2CardEvidence,
  V2CommercialCase,
  V2ConnectedActivity,
  V2ConnectedCase,
  V2ConnectedInterest,
  V2ConnectedQuote,
  V2ConnectionSummary,
  V2Contact,
  V2Organization,
  V2OrganizationCase,
  V2OrganizationCaseRole,
  V2ContactCard,
  V2OrganizationCard,
  V2Quote,
} from "../../api/v2Types";

export function emptyConnectionSummary(
  over: Partial<V2ConnectionSummary> = {},
): V2ConnectionSummary {
  return {
    cases: 0,
    open_cases: 0,
    interests: 0,
    quotes: 0,
    activities: 0,
    case_evidence: 0,
    last_activity_at: null,
    confirmed_people: null,
    ...over,
  };
}

export function cardEvidence(over: Partial<V2CardEvidence> = {}): V2CardEvidence {
  return {
    assertion_id: "a-1",
    kind: "contact_address",
    value_norm: "compras@instituto.invalid",
    resolution: "unresolved",
    resolved_kind: null,
    ambiguity_note: null,
    observed_at: "2026-09-12T10:00:00Z",
    source_kind: "gmail_message",
    source_uri: "gmail://msg/ficticio-1",
    source_review_status: "pending",
    source_is_quarantined: false,
    ...over,
  };
}

export function addressControl(over: Partial<V2AddressControl> = {}): V2AddressControl {
  return {
    value_norm: null,
    control_kind: "prior_contact",
    purpose: "marketing",
    scope: "address",
    reason: "Contacto previo registrado en la unión de seguridad",
    source: "wave1a_union",
    until_at: null,
    needs_review: false,
    created_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

export function contactCard(over: Partial<V2ContactCard> = {}): V2ContactCard {
  return {
    contact_point_id: "96301691-af05-51ea-82e3-05f5fae40837",
    channel_kind: "email",
    address: "compras@instituto.invalid",
    usage: "shared_mailbox",
    confirmation: "machine_proposed",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: null,
    person_id: null,
    person_display_name: null,
    organization_id: "11111111-2222-4333-8444-555555555555",
    organization_name: "Instituto Ficticio de Metrología",
    organization_kind: "unknown",
    origin_source_kind: "migration_manifest",
    origin_source_uri: null,
    origin_review_status: "pending",
    sibling_contact_points: [],
    affiliations: [],
    evidence: [cardEvidence()],
    marketing: [],
    address_controls: [],
    counts: { evidence: 1 },
    cases: [],
    interests: [],
    quotes: [],
    activities: [],
    case_evidence: [],
    connection_summary: emptyConnectionSummary(),
    ...over,
  };
}

export function organizationCard(over: Partial<V2OrganizationCard> = {}): V2OrganizationCard {
  return {
    organization_id: "11111111-2222-4333-8444-555555555555",
    name: "Instituto Ficticio de Metrología",
    legal_name: null,
    kind: "unknown",
    confirmation: "machine_proposed",
    note: null,
    version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: null,
    parent_organization_id: null,
    parent_organization_name: null,
    merged_into_organization_id: null,
    merged_into_organization_name: null,
    origin_source_kind: "migration_manifest",
    origin_source_uri: null,
    origin_review_status: "pending",
    contact_points: [],
    people: [],
    domains: [],
    relationships: [],
    child_organizations: [],
    evidence: [],
    address_controls: [],
    domain_controls: [],
    marketing: [],
    counts: {},
    cases: [],
    interests: [],
    quotes: [],
    activities: [],
    case_evidence: [],
    connection_summary: emptyConnectionSummary(),
    ...over,
  };
}

export function listedQuote(over: Partial<V2Quote> = {}): V2Quote {
  return {
    quote_id: "q-1",
    quote_number: "1235",
    revision_no: 1,
    status: "sent",
    sent_at: "2026-09-15T10:00:00Z",
    opportunity_id: "op-1",
    organization_name: "Instituto Ficticio de Metrología",
    updated_at: null,
    ...over,
  };
}

export function listedCaseFor(
  organizationId: string | null,
  over: Partial<V2CommercialCase> = {},
): V2CommercialCase {
  return {
    opportunity_id: "op-1",
    title: "Caso ficticio 1",
    stage: "qualifying",
    version: 1,
    closed_at: null,
    close_reason: null,
    created_at: "2026-09-10T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    owner_operator_id: "operator-1",
    owner_display_name: "Operador Local",
    origin_source_record_id: "sr-1",
    origin_source_kind: "gmail_message",
    origin_source_uri: "gmail://msg/ficticio-1",
    requesting_organization_id: organizationId,
    requesting_organization_name: organizationId ? "Instituto Ficticio de Metrología" : null,
    requesting_confirmation: organizationId ? "confirmed" : null,
    organization_count: organizationId ? 1 : 0,
    interest_count: 0,
    evidence_count: 1,
    participants: null,
    interest_labels: null,
    quote_count: null,
    latest_quote_status: null,
    last_activity_at: null,
    ...over,
  };
}

/**
 * A case an institution is part of, with the parts it holds on it.
 *
 * Defaults to a single current `supplier` part rather than to `requesting_institution`,
 * because the whole point of the route this fixture stands for is the cases an institution
 * is in *without* being the one asking.
 */
export function organizationCaseWithRoles(
  roles: readonly Partial<V2OrganizationCaseRole>[],
  over: Partial<V2OrganizationCase> = {},
): V2OrganizationCase {
  return {
    ...listedCaseFor(null),
    roles: roles.map((role, index) => ({
      opportunity_organization_id: `oo-${index + 1}`,
      role: "supplier",
      confirmation: "confirmed",
      valid_from: "2026-01-01",
      valid_to: null,
      is_current: true,
      note: null,
      supplier_exception_reason: null,
      ...role,
    })),
    ...over,
  };
}

export function listedOrganization(over: Partial<V2Organization> = {}): V2Organization {
  return {
    organization_id: "11111111-2222-4333-8444-555555555555",
    name: "Instituto Ficticio de Metrología",
    kind: "unknown",
    confirmation: "machine_proposed",
    parent_organization_id: null,
    contact_point_count: 12,
    confirmed_people_count: 0,
    case_count: 0,
    open_case_count: 0,
    interest_count: 0,
    quote_count: 0,
    activity_count: 0,
    last_activity_at: null,
    cases_as_requesting_institution: 0,
    cases_as_end_user_institution: 0,
    cases_as_purchasing_agent: 0,
    cases_as_funder: 0,
    cases_as_supplier: 0,
    cases_as_manufacturer: 0,
    cases_as_mentioned: 0,
    relationship_roles: [],
    created_at: null,
    ...over,
  };
}

export function listedContact(over: Partial<V2Contact> = {}): V2Contact {
  return {
    contact_point_id: "96301691-af05-51ea-82e3-05f5fae40837",
    address: "compras@instituto.invalid",
    usage: "unattributed",
    confirmation: "machine_proposed",
    person_id: null,
    person_display_name: null,
    organization_id: null,
    organization_name: null,
    created_at: null,
    case_count: 0,
    address_control_count: 0,
    ...over,
  };
}

export function connectedCase(over: Partial<V2ConnectedCase> = {}): V2ConnectedCase {
  return {
    opportunity_id: "op-1",
    title: "Caso ficticio 1",
    stage: "lead",
    closed_at: null,
    close_reason: null,
    created_at: "2026-09-10T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    requesting_organization_id: null,
    requesting_organization_name: null,
    roles: ["technical"],
    ...over,
  };
}

export function connectedInterest(over: Partial<V2ConnectedInterest> = {}): V2ConnectedInterest {
  return {
    opportunity_interest_id: "i-1",
    opportunity_id: "op-1",
    opportunity_title: "Caso ficticio 1",
    product_id: null,
    product_model_number: null,
    product_name: null,
    manufacturer_organization_id: null,
    manufacturer_organization_name: null,
    model_text: "Centrífuga CX-1",
    description: null,
    quantity: 2,
    quantity_unit: null,
    confirmation: "machine_proposed",
    created_at: "2026-09-12T00:00:00Z",
    ...over,
  };
}

export function connectedQuote(over: Partial<V2ConnectedQuote> = {}): V2ConnectedQuote {
  return {
    quote_id: "q-1",
    quote_number: "1235",
    opportunity_id: "op-1",
    opportunity_title: "Caso ficticio 1",
    latest_revision_no: 2,
    latest_status: "sent",
    quote_currency: null,
    grand_total: null,
    valid_until: null,
    sent_at: "2026-09-15T10:00:00Z",
    revision_count: 2,
    updated_at: null,
    ...over,
  };
}

export function connectedActivity(over: Partial<V2ConnectedActivity> = {}): V2ConnectedActivity {
  return {
    activity_id: "a-1",
    opportunity_id: "op-1",
    opportunity_title: "Caso ficticio 1",
    kind: "call",
    occurred_at: "2026-09-16T10:00:00Z",
    summary: "Pidió ficha técnica",
    recorded_by_display_name: "Operador Local",
    ...over,
  };
}
