/**
 * Parse V2 durable read-boundary responses.
 *
 * Total-only responses are parsed as totals. Card counts come from `total`, never from
 * `items.length`: the boundary pages at 200, so counting the array would silently cap any
 * card at 200 and quietly understate the operator's workload.
 */

import type {
  V2Contact,
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
