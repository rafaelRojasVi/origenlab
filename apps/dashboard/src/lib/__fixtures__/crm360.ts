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
  V2OrganizationCase,
  V2OrganizationCaseRole,
  V2ContactCard,
  V2OrganizationCard,
  V2Quote,
} from "../../api/v2Types";

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
    counts: {},
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
