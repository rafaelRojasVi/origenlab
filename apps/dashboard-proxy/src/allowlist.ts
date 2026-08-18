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

/**
 * The single POST-permitted upstream path (operator annex-bundle upload
 * preview). Reuses the exact same tender-code character policy
 * (`[A-Za-z0-9-]+`) as the read-only exact tender path above -- never
 * broadened. Kept separate from ALLOWED_UPSTREAM_PATHS so that list can stay
 * exclusively about which GET/HEAD paths are readable, independent of which
 * method a request uses (see index.ts's method-then-path dispatch: POST is
 * checked against this pattern alone, GET/HEAD/OPTIONS against the list
 * above alone -- a path never becomes POST-legal just by matching the list).
 */
export const ANNEX_BUNDLE_PREVIEW_PATH_RE =
  /^\/operator\/procurement\/tenders\/[A-Za-z0-9-]+\/annex-bundle\/preview$/;

export function isAllowedPostUploadPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return ANNEX_BUNDLE_PREVIEW_PATH_RE.test(pathOnly);
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
