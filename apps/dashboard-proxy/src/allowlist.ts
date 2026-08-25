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
  /^\/opportunities\/equipment$/,
  /^\/operations\/work-queue$/,
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
  /^\/operations\/activities$/,
  /^\/operations\/tasks$/,
  /^\/operations\/tasks\/task_[0-9a-f]{32}\/(?:complete|cancel)$/,
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
