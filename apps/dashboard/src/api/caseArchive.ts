/**
 * Read-only client for `GET /v2/cockpit/case-archive` — each commercial case with all its
 * quotations and revisions, where their bytes are in Drive, and their Gmail evidence. Local
 * review only: not in the dashboard-proxy allowlist; the route exists only when the API is
 * started with ORIGENLAB_V2_CASE_ARCHIVE_DIR.
 */

import { fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const CASE_ARCHIVE_PATH = "/v2/cockpit/case-archive";

export type CrmStatus =
  | "not_in_plan"
  | "ready_to_import"
  | "pending_organization_confirmation"
  | "held"
  | "open_in_crm"
  | "closed_won"
  | "closed_lost";

export type ArchiveStatus =
  | "not_archived"
  | "legacy_only"
  | "archived_verified"
  | "archived_unverified"
  | "hash_mismatch";

export type Lifecycle =
  | "historical_sent"
  | "revision"
  | "pending_organization_confirmation"
  | "held"
  | "confirmed_opportunity"
  | "rejected_non_quotation"
  | "closed_won"
  | "closed_lost"
  | "unknown_needs_review";

export interface DriveLink {
  id: string;
  url: string | null;
  path: string | null;
  name: string | null;
  gmail_message_id?: string | null;
}

export interface CaseRevision {
  revision_no: number;
  document_sha256: string;
  sent_at: string | null;
  role: string;
  pending_code: string | null;
  gmail_message_id: string | null;
  gmail_url: string | null;
  lifecycle: Lifecycle;
  lifecycle_label: string;
  archive: { status: ArchiveStatus; file: DriveLink | null; legacy_files: DriveLink[] };
}

export interface CaseCollision {
  number: string;
  kind: "several_cases" | "unplaced_versions";
  cases: string[];
  printed_addressees?: string[];
}

export interface CaseQuote {
  quote_number: string;
  collision: CaseCollision | null;
  revisions: CaseRevision[];
}

export interface CaseReason {
  code: string;
  label: string;
}

export interface ArchiveCase {
  case_key: string;
  opening_quote_number: string | null;
  printed_addressee: string | null;
  printed_organization: string | null;
  folder_name: string | null;
  crm: {
    status: CrmStatus;
    source: "import_plan" | "crm";
    plan_status: string;
    organization_confirmed: boolean;
    organization_action: string | null;
    organization_id: string | null;
    reasons: CaseReason[];
    warnings: CaseReason[];
  };
  archive: {
    folder: DriveLink | null;
    planned_folder_action: string;
    counts: Partial<Record<ArchiveStatus, number>>;
  };
  flags: string[];
  quotes: CaseQuote[];
}

export interface UnplacedDocument {
  path: string;
  classification: string;
  reason: string;
  printed_quote_number: string | null;
  sha256: string | null;
  flags: string[];
  lifecycle: Lifecycle | null;
  action: string;
  drive_id: string;
  url: string | null;
}

export interface CaseArchiveView {
  workspace_version: string;
  source: { dry_run_dir: string; inventory_finished_at: string | null; plan_version: string | null; crm_status_source: string };
  counts: {
    cases: number;
    by_crm_status: Partial<Record<CrmStatus, number>>;
    by_archive_status: Partial<Record<ArchiveStatus, number>>;
    cases_with_collisions: number;
    cases_missing_organization: number;
    unplaced_legacy_documents: number;
  };
  collisions: CaseCollision[];
  cases: ArchiveCase[];
  unplaced_legacy_documents: UnplacedDocument[];
}

export function fetchCaseArchive(): Promise<CaseArchiveView> {
  return fetchJsonGet<CaseArchiveView>(operatorApiUrl(CASE_ARCHIVE_PATH));
}
