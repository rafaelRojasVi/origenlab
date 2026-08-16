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
  /^\/cases\/warm$/,
  /^\/contacts\/[^/]+$/,
  /^\/opportunities\/equipment$/,
  /^\/mirror\/.+/,
];

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
