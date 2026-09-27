/**
 * Invented commercial cases, for tests.
 *
 * Nothing here describes a real case, and nothing could: `crm.opportunity` and the three
 * slice-3 tables are empty in the clean room by declared baseline, and no command has ever
 * been executed against a database this repository can reach. These builders describe
 * shapes the schema permits, with institution names and identifiers that belong to nobody.
 *
 * One copy, imported by every case test, so two tests cannot quietly disagree about what a
 * case looks like.
 */

import type {
  V2CaseEvidence,
  V2CaseInterest,
  V2CaseOrganization,
  V2CommercialCase,
  V2CommercialCaseCard,
} from "../../api/v2Types";

export function organization(over: Partial<V2CaseOrganization> = {}): V2CaseOrganization {
  return {
    opportunity_organization_id: "oo-1",
    organization_id: "org-1",
    name: "Instituto Ficticio de Metrología",
    organization_kind: "unknown",
    role: "mentioned",
    confirmation: "confirmed",
    valid_from: "2026-09-22",
    valid_to: null,
    is_current: true,
    confirmed_by_display_name: "Operador Local",
    note: null,
    origin_source_kind: null,
    origin_source_uri: null,
    supplier_exception_reason: null,
    supplier_exception_at: null,
    supplier_exception_by_display_name: null,
    ...over,
  };
}

export function interest(over: Partial<V2CaseInterest> = {}): V2CaseInterest {
  return {
    opportunity_interest_id: "oi-1",
    product_id: null,
    product_name: null,
    manufacturer_organization_id: null,
    manufacturer_organization_name: null,
    model_text: "Centrífuga ejemplo CX-0",
    description: null,
    quantity: null,
    quantity_unit: null,
    confirmation: "confirmed",
    confirmed_by_display_name: "Operador Local",
    withdrawn_at: null,
    withdraw_reason: null,
    note: null,
    origin_source_kind: null,
    origin_source_uri: null,
    created_at: "2026-09-22T12:00:00Z",
    ...over,
  };
}

export function evidence(over: Partial<V2CaseEvidence> = {}): V2CaseEvidence {
  return {
    opportunity_evidence_id: "oe-1",
    relation: "origin",
    subject_kind: "source_record",
    subject_id: "sr-1",
    source_kind: "gmail_message",
    source_uri: "gmail://msg/ficticio-1",
    source_review_status: "pending",
    assertion_kind: null,
    assertion_value: null,
    linked_by_display_name: "Operador Local",
    linked_at: "2026-09-22T12:00:00Z",
    unlinked_at: null,
    unlink_reason: null,
    note: null,
    ...over,
  };
}

export function card(over: Partial<V2CommercialCaseCard> = {}): V2CommercialCaseCard {
  return {
    opportunity_id: "op-1",
    title: "Caso ficticio 1",
    stage: "lead",
    version: 1,
    closed_at: null,
    close_reason: null,
    created_at: "2026-09-22T12:00:00Z",
    updated_at: "2026-09-22T12:00:00Z",
    owner_operator_id: "operator-1",
    owner_display_name: "Operador Local",
    organization_id: null,
    organization_name: null,
    origin_source_record_id: "sr-1",
    origin_source_kind: "gmail_message",
    origin_source_uri: "gmail://msg/ficticio-1",
    origin_review_status: "pending",
    reopened_from_opportunity_id: null,
    reopened_from_title: null,
    organizations: [],
    interests: [],
    evidence: [evidence()],
    stage_machine: {
      stage: "lead",
      allowed_next_stages: ["qualifying", "abandoned", "lost"],
      is_terminal: false,
      stages_requiring_a_requesting_institution: [
        "qualified",
        "quoting",
        "negotiating",
        "won",
      ],
      stages_requiring_a_close_reason: ["lost", "abandoned"],
    },
    counts: {},
    ...over,
  };
}


/** One row of the case list, which carries counts rather than the child rows themselves. */
export function listedCase(over: Partial<V2CommercialCase> = {}): V2CommercialCase {
  return {
    opportunity_id: "op-1",
    title: "Caso ficticio 1",
    stage: "lead",
    version: 1,
    closed_at: null,
    close_reason: null,
    created_at: "2026-09-22T12:00:00Z",
    updated_at: "2026-09-22T12:00:00Z",
    owner_operator_id: "operator-1",
    owner_display_name: "Operador Local",
    origin_source_record_id: "sr-1",
    origin_source_kind: "gmail_message",
    origin_source_uri: "gmail://msg/ficticio-1",
    requesting_organization_id: null,
    requesting_organization_name: null,
    requesting_confirmation: null,
    organization_count: 0,
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
