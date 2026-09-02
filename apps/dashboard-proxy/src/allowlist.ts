/** Dashboard read-only API paths the Worker may forward upstream. */

export const API_PREFIX = "/api";

export const ALLOWED_UPSTREAM_PATHS: readonly RegExp[] = [
  /^\/health$/,
  /^\/operator\/status$/,
  /^\/operator\/automation-status$/,
  /^\/operator\/procurement\/status$/,
  /^\/operator\/procurement\/institutions$/,
  /^\/operator\/procurement\/institutions\/[^/]+$/,
  /^\/operator\/procurement\/queues\/(?:current_opportunity|historical_prospect|contact_gap|institution_match_review|line_evidence_review|retender_review)$/,
  /^\/operator\/procurement\/tenders\/[A-Za-z0-9-]+$/,
  /^\/operator\/procurement\/tenders\/[A-Za-z0-9-]+\/attachment-navigation$/,
  /^\/cases\/warm$/,
  /^\/contacts\/[^/]+$/,
  // PR3 machine-proposed opportunity intake (read-only list + detail) — the
  // review surface whose human decisions flow through /operations/* below.
  /^\/opportunities\/commercial$/,
  /^\/opportunities\/commercial\/o_[0-9a-f]{32}$/,
  /^\/operations\/work-queue$/,
  /^\/operations\/sales-opportunities$/,
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}$/,
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/activities$/,
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/tasks$/,
  // CRM-Q1 durable customer-quote reads. Quote IDs are service-generated
  // `quote_` + 32 lowercase hex chars. The drive-workspace command path is
  // deliberately NOT GET-readable.
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/quotes$/,
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}$/,
  // CRM backend foundation: global Cotizaciones read (not scoped to a
  // single sales opportunity). Deliberately listed before the per-quote
  // detail regex has no bearing on matching (regex arrays are OR'd, not
  // ordered) but keeps related entries adjacent for readability.
  /^\/operations\/customer-quotes$/,
  // CRM-Q1D follow-up: read-only Drive Pendientes projection for
  // operational visibility. Exact GET path only, no wildcard expansion.
  /^\/operations\/customer-quotes\/drive-pending$/,
  // CRM-Q2: append-only revision-workflow event history for the
  // Cotizaciones drawer. Read-only -- the transition commands below stay
  // POST-only and are never GET-readable.
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/events$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/state$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/activities$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/tasks$/,
  /^\/mirror\/.+/,
];

/**
 * The only POST-permitted upstream paths are the two operator annex-bundle
 * upload actions: preview (non-mutating) and import (explicit persistence).
 *
 * Reuses the exact same tender-code character policy (`[A-Za-z0-9-]+`) as
 * the read-only exact tender path above -- never broadened. Kept separate
 * from ALLOWED_UPSTREAM_PATHS so adding a POST route can never accidentally
 * make any GET-only dashboard route writable.
 */
export const ANNEX_BUNDLE_UPLOAD_PATH_RE =
  /^\/operator\/procurement\/tenders\/[A-Za-z0-9-]+\/annex-bundle\/(?:preview|import)$/;

export function isAllowedPostUploadPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return ANNEX_BUNDLE_UPLOAD_PATH_RE.test(pathOnly);
}

/**
 * Durable commercial operations commands.
 *
 * Keep this separate from both the GET allowlist and annex upload POSTs.
 * Opportunity IDs are PR3 deterministic `o_` + 32 lowercase hex chars.
 * Task IDs are service-generated `task_` + 32 lowercase hex chars.
 */
export const COMMERCIAL_OPERATIONS_POST_PATHS: readonly RegExp[] = [
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/state$/,
  /^\/operations\/sales-opportunities\/promote$/,
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/stage$/,
  /^\/operations\/activities$/,
  /^\/operations\/tasks$/,
  /^\/operations\/tasks\/task_[0-9a-f]{32}\/(?:complete|cancel)$/,
  // CRM-Q1 durable customer-quote commands: create a quote for an existing
  // durable sales opportunity, and retry Drive workspace provisioning.
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/quotes$/,
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/drive-workspace$/,
  // CRM backend foundation: manual (non-PR3) sales-opportunity creation —
  // the Nueva Cotización "create a new opportunity first" path.
  /^\/operations\/sales-opportunities\/manual$/,
  // CRM-Q2 revision-workflow commands. Each endpoint's legal-from-status
  // set is fixed server-side (see apps/api) -- the body never carries a
  // caller-chosen target status, only expected_version.
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/submit-for-review$/,
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/request-adjustments$/,
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/approve$/,
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/confirm-send$/,
  // CRM-Q2B: explicit terminal outcome for a sent quote (Ganada/Nula).
  // Never sends anything, never mutates the linked sales opportunity.
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/close$/,
  // CRM-Q2 "Incorporar al CRM": attach an existing Drive-only folder to a
  // new durable quote under an existing sales opportunity.
  /^\/operations\/sales-opportunities\/sales_[0-9a-f]{32}\/quotes\/adopt-drive-folder$/,
];

export function isAllowedCommercialOperationsPostPath(
  pathname: string,
): boolean {
  const pathOnly = pathname.split("?")[0];
  return COMMERCIAL_OPERATIONS_POST_PATHS.some((pattern) =>
    pattern.test(pathOnly),
  );
}

export function isAllowedPostPath(pathname: string): boolean {
  return (
    isAllowedPostUploadPath(pathname) ||
    isAllowedCommercialOperationsPostPath(pathname)
  );
}

/** Strip `/api` prefix from incoming Worker pathname; null if not under /api. */
export function stripApiPrefix(pathname: string): string | null {
  if (pathname === API_PREFIX) {
    return "/";
  }
  if (!pathname.startsWith(`${API_PREFIX}/`)) {
    return null;
  }
  const upstreamPath = pathname.slice(API_PREFIX.length);
  return upstreamPath.startsWith("/") ? upstreamPath : `/${upstreamPath}`;
}

export function isAllowedUpstreamPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return ALLOWED_UPSTREAM_PATHS.some((pattern) => pattern.test(pathOnly));
}
