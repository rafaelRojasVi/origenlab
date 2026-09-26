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
  // V1 `/contacts/*` and `/mirror/*` are deliberately absent. Upstream they are gated only by
  // the shared API key -- no operator identity, no role, no redaction -- so anyone past
  // Cloudflare Access would read contact addresses unmasked. V2 (`/v2/*` below) is the only
  // browser surface for CRM, contacts and evidence. Re-listing either prefix needs a role
  // model upstream first; `src/allowlist.test.ts` pins both as refused.
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
  // CRM-Q2B: read-only "Incorporar al CRM" intake evidence resolution.
  // Never mutates Drive or durable CRM state -- exact GET path only.
  /^\/operations\/customer-quotes\/drive-pending\/resolve$/,
  // CRM-Q2: append-only revision-workflow event history for the
  // Cotizaciones drawer. Read-only -- the transition commands below stay
  // POST-only and are never GET-readable.
  /^\/operations\/customer-quotes\/quote_[0-9a-f]{32}\/events$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/state$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/activities$/,
  /^\/operations\/opportunities\/o_[0-9a-f]{32}\/tasks$/,
  // V2 durable read boundary. Exact paths only -- deliberately NOT /^\/v2\/.+/, so a
  // route added upstream is never reachable through this Worker until it is listed here
  // by name. Every one of these is GET-only and read-only upstream; the V2 command
  // boundary does not exist yet and must not become reachable by widening this list.
  /^\/v2\/contacts$/,
  /^\/v2\/organizations$/,
  /^\/v2\/prospects$/,
  /^\/v2\/opportunities\/active$/,
  /^\/v2\/tasks\/due$/,
  /^\/v2\/review\/summary$/,
  /^\/v2\/quotes\/followup$/,
  /^\/v2\/evidence$/,
  // The review queue in record form. A distinct literal path, not `/v2/evidence/.+`:
  // widening it here would reach every future sub-resource of a source record, including
  // whatever the V2 command boundary eventually puts there.
  /^\/v2\/evidence\/records$/,
  // The two card routes. A UUID-shaped segment, not `.+`: the Worker still refuses any
  // path it cannot name, and no sub-resource under a card is reachable.
  /^\/v2\/contacts\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  /^\/v2\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  // The one sub-resource under a card, named rather than matched: every case an
  // institution is part of. `/v2/organizations/<uuid>/.+` would have reached whatever
  // else is ever hung under an organization, so the literal `/cases` tail is spelled out.
  /^\/v2\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/cases$/,
  // Commercial cases: the list and one case. Read-only, like everything above it.
  //
  // The V2 command boundary now *does* exist upstream -- six case commands under
  // `POST /v2/commands/*` -- and none of it is added here. That is the whole point of a
  // list of names: a boundary that ships in the API does not thereby ship in the browser,
  // and the dashboard's case screen renders its actions disabled because this Worker
  // permits no POST under `/v2` at all. Widening either list is a separate, deliberate
  // decision with its own review.
  /^\/v2\/cases$/,
  /^\/v2\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  // Dashboard sign-in (Google Workspace, `apps/api` v2/auth_routes.py). Three exact GET
  // paths. The cookie and redirect exceptions they need live in `auth.ts`, and apply to
  // these paths only.
  /^\/auth\/google\/login$/,
  /^\/auth\/google\/callback$/,
  /^\/auth\/session$/,
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

/**
 * Sign-out. Clears the session cookie upstream and writes nothing else; listed apart from
 * the commercial commands so it can never inherit their headers or be mistaken for one.
 */
export const AUTH_LOGOUT_POST_PATH_RE = /^\/auth\/logout$/;

export function isAllowedAuthPostPath(pathname: string): boolean {
  return AUTH_LOGOUT_POST_PATH_RE.test(pathname.split("?")[0]);
}

export function isAllowedPostPath(pathname: string): boolean {
  return (
    isAllowedPostUploadPath(pathname) ||
    isAllowedCommercialOperationsPostPath(pathname) ||
    isAllowedAuthPostPath(pathname)
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
