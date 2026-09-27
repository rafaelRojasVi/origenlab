/**
 * Read-only client for `GET /v2/cockpit/import-review` — a quotation import plan beside the
 * database it was rehearsed into. Local review only: the route is not in the dashboard-proxy
 * allowlist and exists only when the API is started with a plan directory.
 */

import { fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const IMPORT_REVIEW_PATH = "/v2/cockpit/import-review";

export type ImportPlanStatus = "ready" | "waiting" | "held";
export type ImportCompleteness = "complete" | "incomplete" | "not_imported" | "leaked";

export interface ImportAssertion {
  kind: string;
  value: string | null;
  resolution: string | null;
  document_sha256: string | null;
}

export interface ImportEvidence {
  dedupe_key: string;
  in_database: boolean;
  in_ready_scope: boolean;
  source_record_id?: string | null;
  review_status?: string | null;
  is_quarantined?: boolean | null;
  subject?: string | null;
  sender?: string | null;
  recipients?: string | null;
  sent_at?: string | null;
  gmail_thread_id?: string | null;
  gmail_url: string | null;
  gmail_search_url?: string | null;
  assertions?: ImportAssertion[];
}

export interface ImportRevision {
  document_sha256: string;
  filename: string | null;
  sent_at: string | null;
  supersedes_document_sha256: string | null;
  canonical_message: string | null;
  duplicate_messages: string[];
  in_database: boolean;
  quote_id: string | null;
  revision_id: string | null;
  revision_no: number | null;
  status: string | null;
  origin: string | null;
  number_origin: string | null;
  db_origin_message: string | null;
  superseded_by_revision_no: number | null;
  pdf_available: boolean;
}

export interface ImportConfirmation {
  state: "confirmed" | "pending" | "held";
  action?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  confirmed_by?: string | null;
  operator_id?: string | null;
  role?: string | null;
  confirmed_at?: string | null;
  category?: string | null;
  note?: string | null;
}

export interface ImportOpportunity {
  planned_opportunity_id: string;
  plan_status: ImportPlanStatus;
  plan_status_raw: string;
  reasons: string[];
  warnings: { code: string; [k: string]: unknown }[];
  printed_organization: string | null;
  printed_addressee: string | null;
  recipient_addresses: string[];
  confirmation: ImportConfirmation;
  opportunity: { id: string; title: string; stage: string; version: number } | null;
  organizations: { organization_id: string; name: string; role: string; confirmation: string }[];
  quotes: { quote_number: string; revisions: ImportRevision[] }[];
  evidence: ImportEvidence[];
  checks: { code: string; ok: boolean }[];
  completeness: ImportCompleteness;
  commands_planned: number;
  receipts_in_database: number;
}

export interface ImportInvariant {
  ok: boolean;
  violations: Record<string, unknown>[];
}

export interface ImportReview {
  review_version: string;
  plan: {
    sha256: string;
    version: string;
    evidence_scope: string;
    generated_at: string | null;
    dry_run_target: string | null;
    summary: Record<string, unknown>;
  };
  database: string;
  operators: { id: string; email: string; role: string; status: string }[];
  counts: Record<string, number>;
  invariants: Record<
    "waiting_or_held_in_database" | "unready_evidence_in_ready_opportunity" | "ready_opportunity_incomplete",
    ImportInvariant
  >;
  unplanned: {
    opportunities: { id: string; title: string; stage: string }[];
    revisions: { quote_number: string; opportunity_id: string; pdf_sha256: string | null }[];
  };
  opportunities: ImportOpportunity[];
}

export function fetchImportReview(): Promise<ImportReview> {
  return fetchJsonGet<ImportReview>(operatorApiUrl(IMPORT_REVIEW_PATH));
}

/** The revision PDF, served only for a document the database holds and whose bytes hash right. */
export function importRevisionPdfUrl(sha256: string): string | null {
  return /^[0-9a-f]{64}$/.test(sha256)
    ? operatorApiUrl(`${IMPORT_REVIEW_PATH}/documents/${sha256}`)
    : null;
}
