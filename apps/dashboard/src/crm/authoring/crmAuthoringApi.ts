/**
 * CRM authoring client: all 27 CRM commands and 3 authoring reads.
 *
 * This module owns EVERY `/v2/commands/<crm-authoring command>` path string — pinned by
 * `src/test/noWritePolicy.test.ts`. No other dashboard module may name these paths.
 */

import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";

/* ── command paths ─────────────────────────────────────────────────────── */

export const CRM_COMMAND_PATHS = {
  // person
  createPerson: "/v2/commands/create-person",
  updatePerson: "/v2/commands/update-person",
  archivePerson: "/v2/commands/archive-person",
  restorePerson: "/v2/commands/restore-person",
  mergePeople: "/v2/commands/merge-people",
  // contact point
  addContactPoint: "/v2/commands/add-contact-point",
  updateContactPoint: "/v2/commands/update-contact-point",
  deactivateContactPoint: "/v2/commands/deactivate-contact-point",
  // affiliations
  linkPersonOrganization: "/v2/commands/link-person-organization",
  unlinkPersonOrganization: "/v2/commands/unlink-person-organization",
  // organization
  registerOrganization: "/v2/commands/register-organization",
  updateOrganization: "/v2/commands/update-organization",
  archiveOrganization: "/v2/commands/archive-organization",
  restoreOrganization: "/v2/commands/restore-organization",
  addOrganizationIdentifier: "/v2/commands/add-organization-identifier",
  removeOrganizationIdentifier: "/v2/commands/remove-organization-identifier",
  addOrganizationDomain: "/v2/commands/add-organization-domain",
  removeOrganizationDomain: "/v2/commands/remove-organization-domain",
  addOrganizationClassification: "/v2/commands/add-organization-classification",
  removeOrganizationClassification: "/v2/commands/remove-organization-classification",
  linkOrganizationProductLine: "/v2/commands/link-organization-product-line",
  unlinkOrganizationProductLine: "/v2/commands/unlink-organization-product-line",
  // supplier candidates
  confirmSupplierCandidate: "/v2/commands/confirm-supplier-candidate",
  rejectSupplierCandidate: "/v2/commands/reject-supplier-candidate",
  // notes
  addNote: "/v2/commands/add-note",
  reviseNote: "/v2/commands/revise-note",
  archiveNote: "/v2/commands/archive-note",
} as const;

/* ── read paths ─────────────────────────────────────────────────────────── */

export const CRM_READ_PATHS = {
  personAuthoring: (id: string) => `/v2/workspace/people/${encodeURIComponent(id)}`,
  organizationAuthoring: (id: string) => `/v2/workspace/organizations/${encodeURIComponent(id)}/authoring`,
  mergePreview: (loser: string, winner: string) =>
    `/v2/workspace/people/merge-preview?loser=${encodeURIComponent(loser)}&winner=${encodeURIComponent(winner)}`,
} as const;

/* ── response types ─────────────────────────────────────────────────────── */

export interface CommandReceipt {
  ok: true;
  replayed: boolean;
  idempotency_key: string;
  command_receipt_id: string;
  version?: number;
  [key: string]: unknown;
}

export interface NoteRow {
  id: string;
  root_note_id: string;
  revision_no: number;
  body: string;
  author_operator_id: string;
  author_name: string;
  created_at: string;
  status: "active" | "archived";
  archived_at: string | null;
  archive_reason: string | null;
  version: number;
  is_latest: boolean;
}

export interface ContactPointRow {
  id: string;
  kind: "email" | "phone";
  value_display: string;
  value_norm: string;
  usage: string | null;
  status: "active" | "inactive";
  version: number;
  note: string | null;
  deactivated_at: string | null;
  created_at: string;
}

export interface AffiliationRow {
  id: string;
  organization_id: string;
  organization_name: string;
  role_title: string | null;
  unit_label: string | null;
  valid_from: string | null;
  valid_to: string | null;
  confirmation: string;
  note: string | null;
}

export interface PersonReferences {
  campaign_recipients: number;
  opportunity_participants: number;
  quotes: number;
  evidence_assertions: number;
  notes: number;
}

export interface PersonAuthoringResponse {
  person: {
    id: string;
    display_name: string;
    given_name: string | null;
    family_name: string | null;
    title: string | null;
    status: "active" | "archived";
    archived_at: string | null;
    archive_reason: string | null;
    confirmation: string;
    version: number;
    created_at: string | null;
    updated_at: string | null;
    merged_into_person_id: string | null;
  };
  contact_points: ContactPointRow[];
  affiliations: AffiliationRow[];
  notes: NoteRow[];
  references: PersonReferences;
  removal: { allowed: false; reasons: string[] };
  authoring: { enabled: boolean; may_author: boolean; may_archive: boolean };
}

export interface IdentifierRow {
  id: string;
  scheme: string;
  value_norm: string;
  removed_at: string | null;
  remove_reason: string | null;
}

export interface DomainRow {
  id: string;
  domain_norm: string;
  scope: string;
  removed_at: string | null;
  remove_reason: string | null;
}

export interface ClassificationRow {
  id: string;
  role: string;
  valid_from: string | null;
  valid_to: string | null;
  note: string | null;
}

export interface ProductLineRow {
  id: string;
  line_id: string;
  line_name: string;
  valid_from: string | null;
  valid_to: string | null;
  note: string | null;
}

export interface OrgPersonRow {
  person_id: string;
  display_name: string;
  role_title: string | null;
  valid_from: string | null;
  valid_to: string | null;
  affiliation_id: string;
}

export interface OrgReferences {
  campaign_recipients: number;
  opportunities: number;
  quotes: number;
  affiliations: number;
  evidence_assertions: number;
  catalog_products: number;
}

export interface OrganizationAuthoringResponse {
  organization: {
    id: string;
    name: string;
    legal_name: string | null;
    kind: string;
    status: "active" | "archived";
    archived_at: string | null;
    archive_reason: string | null;
    confirmation: string;
    version: number;
    merged_into_organization_id: string | null;
    created_at: string | null;
  };
  identifiers: IdentifierRow[];
  domains: DomainRow[];
  classifications: ClassificationRow[];
  product_lines: ProductLineRow[];
  contact_points: ContactPointRow[];
  people: OrgPersonRow[];
  notes: NoteRow[];
  references: OrgReferences;
  removal: { allowed: false; reasons: string[] };
  authoring: { enabled: boolean; may_author: boolean; may_archive: boolean };
}

export interface MergePreviewResponse {
  loser: { id: string; display_name: string; version: number };
  winner: { id: string; display_name: string; version: number };
  moves: {
    contact_points: number;
    affiliations: number;
    opportunity_participants: number;
    campaign_recipients: number;
    notes: number;
  };
  conflicts: string[];
  preview_sha256: string;
}

/* ── helpers ─────────────────────────────────────────────────────────────── */

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `crm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function postCommand<T>(path: string, body: unknown, idempotencyKey: string): Promise<T> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

/** The API's refusal `{detail: {code, message}}`, when the error carries one. */
export function refusalOf(err: unknown): { code: string; message: string } | null {
  if (!(err instanceof OperatorApiError)) return null;
  if (err.message.includes("path_not_allowed")) return { code: "path_not_allowed", message: err.message };
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown };
    const d = parsed.detail;
    if (d && typeof d === "object" && "code" in d && "message" in d) {
      return { code: String((d as { code: unknown }).code), message: String((d as { message: unknown }).message) };
    }
    if (typeof d === "string") return { code: `http_${err.status}`, message: d };
  } catch {
    /* not JSON */
  }
  return { code: `http_${err.status}`, message: err.message };
}

/* ── reads ───────────────────────────────────────────────────────────────── */

export const fetchPersonAuthoring = (personId: string) =>
  fetchJsonGet<PersonAuthoringResponse>(operatorApiUrl(CRM_READ_PATHS.personAuthoring(personId)));

export const fetchOrganizationAuthoring = (organizationId: string) =>
  fetchJsonGet<OrganizationAuthoringResponse>(operatorApiUrl(CRM_READ_PATHS.organizationAuthoring(organizationId)));

export const fetchMergePreview = (loser: string, winner: string) =>
  fetchJsonGet<MergePreviewResponse>(operatorApiUrl(CRM_READ_PATHS.mergePreview(loser, winner)));

/* ── person commands ─────────────────────────────────────────────────────── */

export interface CreatePersonBody {
  display_name: string;
  given_name?: string | null;
  family_name?: string | null;
  title?: string | null;
  email?: string | null;
  phone?: string | null;
  organization_id?: string | null;
  role_title?: string | null;
  note: string;
}

export function createPerson(
  body: CreatePersonBody,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.createPerson, body, idempotencyKey);
}

export interface UpdatePersonBody {
  person_id: string;
  expected_version: number;
  display_name?: string;
  given_name?: string | null;
  family_name?: string | null;
  title?: string | null;
  note: string;
}

export function updatePerson(
  body: UpdatePersonBody,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.updatePerson, body, idempotencyKey);
}

export function archivePerson(
  body: { person_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.archivePerson, body, idempotencyKey);
}

export function restorePerson(
  body: { person_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.restorePerson, body, idempotencyKey);
}

export function mergePeople(
  body: {
    loser_person_id: string;
    winner_person_id: string;
    expected_loser_version: number;
    expected_winner_version: number;
    expected_preview_sha256: string;
    confirmed: true;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.mergePeople, body, idempotencyKey);
}

/* ── contact point commands ──────────────────────────────────────────────── */

export function addContactPoint(
  body: {
    person_id: string;
    expected_version: number;
    kind: "email" | "phone";
    value: string;
    usage?: string | null;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.addContactPoint, body, idempotencyKey);
}

export function updateContactPoint(
  body: { contact_point_id: string; expected_version: number; usage?: string | null; value_display?: string | null; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.updateContactPoint, body, idempotencyKey);
}

export function deactivateContactPoint(
  body: { contact_point_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.deactivateContactPoint, body, idempotencyKey);
}

/* ── affiliation commands ────────────────────────────────────────────────── */

export function linkPersonOrganization(
  body: {
    person_id: string;
    expected_version: number;
    organization_id: string;
    role_title?: string | null;
    unit_label?: string | null;
    valid_from?: string | null;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.linkPersonOrganization, body, idempotencyKey);
}

export function unlinkPersonOrganization(
  body: {
    person_id: string;
    expected_version: number;
    affiliation_id: string;
    valid_to?: string | null;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.unlinkPersonOrganization, body, idempotencyKey);
}

/* ── organization commands ───────────────────────────────────────────────── */

export const PRODUCT_LINES = ["hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"] as const;
export type ProductLineId = (typeof PRODUCT_LINES)[number];

export interface RegisterOrganizationBody {
  name: string;
  legal_name?: string | null;
  kind: string;
  classification?: string | null;
  domain?: string | null;
  product_lines?: ProductLineId[];
  note: string;
}

export function registerOrganization(
  body: RegisterOrganizationBody,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.registerOrganization, body, idempotencyKey);
}

export function updateOrganization(
  body: {
    organization_id: string;
    expected_version: number;
    name?: string;
    legal_name?: string | null;
    kind?: string;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.updateOrganization, body, idempotencyKey);
}

export function archiveOrganization(
  body: { organization_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.archiveOrganization, body, idempotencyKey);
}

export function restoreOrganization(
  body: { organization_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.restoreOrganization, body, idempotencyKey);
}

export function addOrganizationIdentifier(
  body: { organization_id: string; expected_version: number; scheme: string; value: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.addOrganizationIdentifier, body, idempotencyKey);
}

export function removeOrganizationIdentifier(
  body: { organization_id: string; expected_version: number; identifier_id: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.removeOrganizationIdentifier, body, idempotencyKey);
}

export function addOrganizationDomain(
  body: { organization_id: string; expected_version: number; domain: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.addOrganizationDomain, body, idempotencyKey);
}

export function removeOrganizationDomain(
  body: { organization_id: string; expected_version: number; domain_id: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.removeOrganizationDomain, body, idempotencyKey);
}

export function addOrganizationClassification(
  body: {
    organization_id: string;
    expected_version: number;
    role: string;
    valid_from?: string | null;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.addOrganizationClassification, body, idempotencyKey);
}

export function removeOrganizationClassification(
  body: {
    organization_id: string;
    expected_version: number;
    relationship_id: string;
    valid_to?: string | null;
    note: string;
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.removeOrganizationClassification, body, idempotencyKey);
}

export function linkOrganizationProductLine(
  body: { organization_id: string; expected_version: number; line_id: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.linkOrganizationProductLine, body, idempotencyKey);
}

export function unlinkOrganizationProductLine(
  body: { organization_id: string; expected_version: number; link_id: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.unlinkOrganizationProductLine, body, idempotencyKey);
}

/* ── supplier candidate commands ─────────────────────────────────────────── */

export interface ConfirmSupplierCandidateBody {
  assertion_id: string;
  organization_id?: string | null;
  new_organization?: { name: string; kind: string } | null;
  classification: "supplier" | "manufacturer";
  product_lines?: ProductLineId[];
  note: string;
}

export function confirmSupplierCandidate(
  body: ConfirmSupplierCandidateBody,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.confirmSupplierCandidate, body, idempotencyKey);
}

export function rejectSupplierCandidate(
  body: { assertion_id: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.rejectSupplierCandidate, body, idempotencyKey);
}

/* ── note commands ───────────────────────────────────────────────────────── */

export function addNote(
  body: { subject_kind: "person" | "organization" | "opportunity"; subject_id: string; body: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.addNote, body, idempotencyKey);
}

export function reviseNote(
  body: { note_id: string; expected_version: number; body: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.reviseNote, body, idempotencyKey);
}

export function archiveNote(
  body: { note_id: string; expected_version: number; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandReceipt> {
  return postCommand<CommandReceipt>(CRM_COMMAND_PATHS.archiveNote, body, idempotencyKey);
}

export { newIdempotencyKey };
