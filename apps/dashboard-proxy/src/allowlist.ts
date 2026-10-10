/** Dashboard read-only API paths the Worker may forward upstream. */

export const API_PREFIX = "/api";

export const ALLOWED_UPSTREAM_PATHS: readonly RegExp[] = [
  /^\/health$/,
  // V1 surfaces (`/operator/*`, `/cases/warm`, `/opportunities/*`, `/operations/*`,
  // `/contacts/*`, `/mirror/*`) are deliberately absent. Upstream they are gated only by the
  // shared API key -- no operator identity, no role, no redaction. Once Cloudflare Access is
  // retired from the dashboard hostname (OPERATIONS.md §1.2 step 10), the API session is the only identity this
  // Worker carries, so only routes that resolve an operator from that session are listed:
  // `/auth/*` and the named `/v2/*` reads below. `src/allowlist.test.ts` pins every V1 path as refused.
  // V2 durable read boundary. Exact paths only -- deliberately NOT /^\/v2\/.+/, so a
  // route added upstream is never reachable through this Worker until it is listed here
  // by name. Every one of these is GET-only and read-only upstream. The V2 command boundary
  // is reached only through the named POST lists further down, never by widening this one.
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
  // Nine case commands exist upstream under `POST /v2/commands/*`; this read list names
  // none of them. Six -- advance-case-stage, add-case-organization, set-case-organization-role, record-case-won, resolve-current-revision and
  // record-case-quotation -- are reachable as POSTs through `CASE_COMMAND_POST_PATHS` below
  // (with the three W11 task commands); the other three case commands stay refused. Widening either
  // list is a separate, deliberate decision with its own review.
  /^\/v2\/cases$/,
  /^\/v2\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  // CRM Marketing reads (`apps/api` v2/crm_workspace_routes.py). Exact paths only: the
  // campaign list, the equipment taxonomy, the interest audience, one campaign's content, the
  // audience-freeze preview, a frozen campaign's recipient snapshot and its sent-HTML archive.
  // Addresses in them are
  // masked for a `viewer` upstream (contact_redaction.py). Nothing else under
  // `/v2/workspace/*` is listed.
  /^\/v2\/workspace\/marketing$/,
  /^\/v2\/workspace\/marketing\/taxonomy$/,
  /^\/v2\/workspace\/marketing\/audience$/,
  /^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  /^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/freeze-preview$/,
  /^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/recipients$/,
  // The sent-HTML archive: a campaign's frozen content (only when its fingerprint recomputes)
  // and its real send batches. A read; the dashboard renders the HTML sandboxed.
  /^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/archive$/,
  // One campaign's recorded history, for its detail tabs: a page of recipients filtered by the
  // same predicate as the total clicked (a `viewer` searches names only and reads masked
  // addresses), the replies and «BAJA» stored with lineage, and its audit trail. Reads only;
  // Gmail is never called.
  /^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/history\/(recipients|replies|audit)$/,
  // W10 suppression status: which addresses asked for «BAJA», since when, and how many frozen
  // recipients it refuses today. A read, masked for a `viewer` upstream, never a message body.
  // The unsubscribe preview (`POST /v2/unsubscribe/preview`) and apply
  // (`POST /v2/commands/apply-unsubscribe-replies`) are deliberately NOT listed anywhere: they
  // carry message bodies and stay operator tooling on the API. The two review decisions are
  // POST-only marketing commands (MARKETING_COMMAND_POST_PATHS), never GET-readable.
  /^\/v2\/workspace\/marketing\/suppressions$/,
  // Campaign safety blocks (WORKFLOWS.md §W13): every active block and each target's version.
  // Reasons and operators are withheld from a viewer upstream.
  /^\/v2\/workspace\/marketing\/campaign-blocks$/,
  // Test send history: a read-only record of test emails sent for a campaign. GET only.
  /^\/v2\/workspace\/marketing\/test-send-history$/,
  // CRM card reads (`apps/api` v2/crm_workspace_routes.py): the supplier directory with its
  // machine candidates, and the observed equipment interests per line, institution and
  // destination. Two literal paths, GET-only upstream; addresses are masked for a `viewer`
  // upstream and masked destinations join only by an opaque keyed `address_ref`.
  /^\/v2\/workspace\/providers$/,
  /^\/v2\/workspace\/equipment-interests$/,
  // CRM authoring reads (ContactRedactingRoute; viewer sees addresses masked as ***@domain).
  // Three exact paths: person detail, organization authoring detail, and merge preview (with
  // query string). Named individually — not `/v2/workspace/.+` — so nothing else becomes
  // reachable until it is reviewed and listed here by name.
  /^\/v2\/workspace\/people\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  /^\/v2\/workspace\/people\/merge-preview$/,
  /^\/v2\/workspace\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/authoring$/,
  // People the quote emails name and the CRM does not hold yet. A read: nothing is created until
  // an operator sends `create-person`. Addresses masked for a `viewer` upstream.
  /^\/v2\/workspace\/person-suggestions$/,
  // The notes on one case, for the case drawer's «Registrar seguimiento» (`apps/api`
  // v2/crm_workspace_routes.py). GET-only, operator session required; a UUID-shaped segment and
  // the literal `/notes` tail, so nothing else under `/v2/workspace/opportunities` is reachable.
  // Writing a note is `add-note` (CRM authoring), never this path.
  /^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/notes$/,
  // The Gmail messages linked to one case and the documents they carry, for «Registrar
  // cotización» / «Nueva revisión». GET-only, operator session required, the same UUID-shaped
  // segment and a literal `/mail-documents` tail. Recording a quote is `record-case-quotation`.
  /^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/mail-documents$/,
  // Strict read-only candidates for a PDF quoted in a different Gmail thread.
  // Never links evidence or writes a quote; exact UUID and literal tail only.
  /^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/quote-candidates$/,
  // Operator-reviewed standalone purchase order suggestions: read-only and UUID-scoped.
  /^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/purchase-order-candidates$/,
  // The rest of the CRM workspace (dashboard `#/crm/*`): four literal read paths and the one
  // cockpit read the CRM's review screen uses. Upstream each is GET-only, resolves the operator
  // from the dashboard session cookie (401 without one), and masks every email and phone for a
  // `viewer` (`ContactRedactingRoute`). Named one by one -- not `/v2/workspace/.+` or
  // `/v2/cockpit/.+` -- so the other cockpit reads, the quotation PDF route and the
  // case-archive/import-review consoles stay refused until they are reviewed on their own.
  /^\/v2\/workspace\/overview$/,
  /^\/v2\/workspace\/pipeline$/,
  /^\/v2\/workspace\/drive$/,
  /^\/v2\/workspace\/review$/,
  // Exchange rates for the Resumen (dólar observado, euro, UF): Banco Central figures the API
  // reads from mindicador.cl and caches for an hour. Same GET-only, operator-session rule as the
  // reads above; it carries no contact data. Nothing else under /fx is reachable.
  /^\/v2\/workspace\/fx$/,
  // Gmail capture status for the dashboard banner (Phase 4a): state and last sync time of the one
  // captured mailbox. Same GET-only, operator-session rule as the reads above; it carries no
  // address and no message. Nothing under it is reachable.
  /^\/v2\/workspace\/mail-sync$/,
  // Quote numbers already seen in the captured Gmail (number, first sighting, message count), so
  // the Resumen's quote-number box never suggests a number already sent. Same GET-only,
  // operator-session rule; no address, subject or institution. Nothing under it is reachable.
  /^\/v2\/workspace\/mail-quote-numbers$/,
  // «Historial»: the recorded decisions in plain Spanish (no notes, no addresses).
  /^\/v2\/workspace\/history$/,
  // Email → cases dry run (spec 2026-10-05): what the rules would do, with their reasons, and the
  // actions already applied. Admin only upstream (403 for sales and viewer); writes nothing.
  /^\/v2\/workspace\/mail-rules\/preview$/,
  // Mail triage review (`apps/api` v2/triage_review.py): the worker's suggestions for the mail a
  // person wrote, with their email, products and case stage (`?status=&limit=`). Any operator
  // reads; GET-only upstream through the contact-redacting route. Nothing under it is reachable.
  /^\/v2\/workspace\/triage-readings$/,
  /^\/v2\/cockpit\/work-queue$/,
  // Catalog 1a reads (`apps/api` v2/catalog/routes.py): products, one product, the supplier list
  // (names and product counts by kind, no costs), a supplier's terms, cost parameters, FX, price history (`?model_key=&limit=`) and a short-lived signed
  // image URL (JSON, never image bytes). Exact paths, GET-only upstream; named one by one so
  // nothing else under `/v2/catalog` is reachable until it is reviewed and listed.
  /^\/v2\/catalog\/products$/,
  /^\/v2\/catalog\/products\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  /^\/v2\/catalog\/suppliers$/,
  /^\/v2\/catalog\/suppliers\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/terms$/,
  /^\/v2\/catalog\/parameters$/,
  /^\/v2\/catalog\/fx$/,
  /^\/v2\/catalog\/price-history$/,
  /^\/v2\/catalog\/images\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/url$/,
  // Dashboard sign-in (Google Workspace, `apps/api` v2/auth_routes.py). Three exact GET
  // paths. The cookie and redirect exceptions they need live in `auth.ts`, and apply to
  // these paths only.
  /^\/auth\/google\/login$/,
  /^\/auth\/google\/callback$/,
  /^\/auth\/session$/,
  // The profile screen behind a shared Workspace sign-in (`apps/api` v2/profile_routes.py): the
  // signed-in principal's usable profiles. GET-only; the session cookie resolves the principal
  // upstream and nothing in the request can name another one.
  /^\/auth\/profiles$/,
];

/**
 * CRM Marketing commands: create and save a campaign draft, freeze a draft's audience into an
 * immutable recipient snapshot, set an unsent campaign's internal planned day, and place or lift
 * a campaign safety block (admin only upstream). Eight exact paths; none approves, schedules or
 * sends anything — a block only refuses — and no send, approve or activate command exists
 * upstream to list.
 *
 * Upstream each requires an active `sales` or `admin` operator (resolved from the verified
 * session, never the body), an `Idempotency-Key`, and a
 * compare-and-set `expected_version` (save, freeze); the freeze also requires `confirmed: true`
 * and the preview fingerprint the operator was shown. Each mounts only behind its own API
 * switch. Every request here must also pass `marketingCommandRefusal` in index.ts: an allowed
 * `Origin`, no cross-site `Sec-Fetch-Site`, a JSON body within `marketingCommandMaxBytes(path)`,
 * and a well-formed `Idempotency-Key` — the CSRF and replay guard for cookie sessions.
 */
export const MARKETING_COMMAND_POST_PATHS: readonly RegExp[] = [
  /^\/v2\/commands\/create-campaign-draft$/,
  /^\/v2\/commands\/save-campaign-draft$/,
  /^\/v2\/commands\/freeze-campaign-audience$/,
  /^\/v2\/commands\/set-campaign-planning$/,
  // W10 review of a «BAJA» held for review. Upstream: confirm needs an active sales/admin
  // operator, dismiss an active admin plus the review's review_sha256; both a note or
  // explanation, and the database re-checks every one of those. Neither lifts a confirmed
  // unsubscribe; nothing here applies replies or reads a mailbox.
  /^\/v2\/commands\/resolve-unsubscribe-review$/,
  /^\/v2\/commands\/dismiss-unsubscribe-review$/,
  // W10: confirm one mail-triage «baja» reading (a «REMOVER» subject, a «BAJA» first line) as the
  // same permanent suppression. Upstream: an active sales/admin operator, the reading's id, its
  // sender and a note; the database proves the reading is of an inbound email from that address.
  /^\/v2\/commands\/confirm-triage-unsubscribe$/,
  /^\/v2\/commands\/block-campaign$/,
  /^\/v2\/commands\/unblock-campaign$/,
  // The only sending path: one test, one address, admin-only upstream.
  /^\/v2\/commands\/send-campaign-test$/,
];

/** Larger than the API's 512 kB HTML limit plus the freeze's 5,000 decisions of 500 characters. */
export const MARKETING_COMMAND_MAX_BYTES = 3_500_000;

/** A planning body is a UUID, a version, a date and a time: a few hundred bytes. */
export const CAMPAIGN_PLANNING_MAX_BYTES = 4_096;

const CAMPAIGN_PLANNING_PATH_RE = /^\/v2\/commands\/set-campaign-planning$/;

/** A review decision is a UUID, an address, a hash and at most 1,000 characters of text. */
export const UNSUBSCRIBE_REVIEW_MAX_BYTES = 8_192;

const UNSUBSCRIBE_REVIEW_PATH_RE = /^\/v2\/commands\/((resolve|dismiss)-unsubscribe-review|confirm-triage-unsubscribe)$/;

/** A block body is a scope, a UUID, a version and a reason of at most 2,000 characters. */
export const CAMPAIGN_BLOCK_MAX_BYTES = 16_384;

const CAMPAIGN_BLOCK_PATH_RE = /^\/v2\/commands\/(?:block|unblock)-campaign$/;

const TEST_SEND_PATH_RE = /^\/v2\/commands\/send-campaign-test$/;

/**
 * Email → cases commands (spec 2026-10-05): apply the rules' `auto` actions, undo one applied
 * action, or switch the automatic R1/R2 run on or off (`set-auto-mail-rules`), plus the mail-triage verdict
 * (`review-triage`, sales or admin). Four exact paths. Upstream all three are admin-only, need an `Idempotency-Key`, and the apply
 * re-plans on the server — the browser never sends an action, at most the evidence ids to limit
 * the run to. The case commands the rules call stay unreachable from the browser except the two
 * the case drawer uses (`CASE_COMMAND_POST_PATHS`). Same
 * Origin / JSON / key guard as the marketing commands.
 */
export const MAIL_RULES_COMMAND_POST_PATHS: readonly RegExp[] = [
  /^\/v2\/commands\/apply-mail-rules$/,
  /^\/v2\/commands\/undo-mail-rule-action$/,
  /^\/v2\/commands\/set-auto-mail-rules$/,
  // A person's verdict on one mail-triage suggestion (approve, correct, reject; optional note).
  // `sales` or `admin` upstream, `Idempotency-Key`, append-only; it moves no case.
  /^\/v2\/commands\/review-triage$/,
];

/** At most 2,000 evidence ids, or a receipt id and a note. */
export const MAIL_RULES_MAX_BYTES = 131_072;

export function isAllowedMailRulesCommandPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return MAIL_RULES_COMMAND_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

/**
 * Commercial-case commands the case drawer uses: «Cambiar etapa» (`advance-case-stage`),
 * «Marcar ganada» (`record-case-won`), «Elegir revisión vigente» (`resolve-current-revision`)
 * and «Registrar cotización» / «Nueva revisión» (`record-case-quotation`), plus the three W11
 * task commands behind «En pausa hasta…» (`create-task`), «Retomar ahora» (`cancel-task`) and
 * «Hecho» (`complete-task`), and «Abrir caso» on a «Correos sin caso» row (`open-commercial-case`:
 * a case at `lead` whose origin is that email, owner decision 2026-10-10), and «Reabrir» on a reply to a
 * closed case (`reopen-commercial-case`: a new case that references the closed one, WORKFLOWS §1.1).
 * Twelve exact paths, and only twelve. Upstream each needs an active `sales` or `admin` operator (from the verified session, never
 * the body), an `Idempotency-Key`, the case (or task) version the operator was shown (the opening
 * command names its origin record instead) and a note, and mounts only behind
 * `ORIGENLAB_V2_COMMANDS_ENABLED`. `record-case-interest` stays refused. The reviewed cross-thread quotation flow
 * explicitly links an existing Gmail evidence record via link-case-evidence; the
 * operator must submit a case version, a written note and an idempotency key. Same Origin / JSON / key guard as the
 * marketing commands (`marketingCommandRefusal` in index.ts).
 */
export const CASE_COMMAND_POST_PATHS: readonly RegExp[] = [
  /^\/v2\/commands\/advance-case-stage$/,
  /^\/v2\/commands\/add-case-organization$/,
  /^\/v2\/commands\/set-case-organization-role$/,
  /^\/v2\/commands\/record-case-won$/,
  /^\/v2\/commands\/resolve-current-revision$/,
  /^\/v2\/commands\/record-case-quotation$/,
  /^\/v2\/commands\/link-case-evidence$/,
  /^\/v2\/commands\/create-task$/,
  /^\/v2\/commands\/complete-task$/,
  /^\/v2\/commands\/cancel-task$/,
  /^\/v2\/commands\/open-commercial-case$/,
  /^\/v2\/commands\/reopen-commercial-case$/,
];

/** A case command is UUIDs, a version, a stage, a revision or a quote number, and short texts. */
export const CASE_COMMAND_MAX_BYTES = 16_384;

export function isAllowedCaseCommandPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return CASE_COMMAND_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

/** The body limit for one marketing (or email-rules, or case) command path. */
export function marketingCommandMaxBytes(pathname: string): number {
  const pathOnly = pathname.split("?")[0];
  if (isAllowedMailRulesCommandPostPath(pathOnly)) return MAIL_RULES_MAX_BYTES;
  if (isAllowedCaseCommandPostPath(pathOnly)) return CASE_COMMAND_MAX_BYTES;
  if (CAMPAIGN_PLANNING_PATH_RE.test(pathOnly)) return CAMPAIGN_PLANNING_MAX_BYTES;
  if (UNSUBSCRIBE_REVIEW_PATH_RE.test(pathOnly)) return UNSUBSCRIBE_REVIEW_MAX_BYTES;
  if (CAMPAIGN_BLOCK_PATH_RE.test(pathOnly)) return CAMPAIGN_BLOCK_MAX_BYTES;
  if (TEST_SEND_PATH_RE.test(pathOnly)) return 4_096;
  return MARKETING_COMMAND_MAX_BYTES;
}

export function isAllowedMarketingCommandPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return MARKETING_COMMAND_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

/**
 * CRM authoring commands: freeform create/update/archive/restore/merge of person/organization/
 * contact_point, classification and product-line links, supplier-candidate resolution, and
 * notes (add, revise, archive), and the explicit restore of a soft-removed organization domain.
 * Twenty-nine exact paths under `/v2/commands/`, `confirm-organization-record` (the card's
 * «Confirmar institución», a record-level confirm that needs no assertion) among them. None is
 * evidence-bound, and none of the evidence-bound commands (create-organization, confirm-
 * organization, attach-contact-address, attribute-sender-organization, confirm-person-from-
 * evidence, the case commands, apply-unsubscribe-replies, preview) is listed here — the six case
 * commands the drawer uses have their own list (`CASE_COMMAND_POST_PATHS`); the rest stay
 * refused on the browser boundary until reviewed separately.
 *
 * Every request here must pass `marketingCommandRefusal` in index.ts: an allowed `Origin`, no
 * cross-site `Sec-Fetch-Site`, a JSON body within `CRM_AUTHORING_MAX_BYTES`, and a
 * well-formed `Idempotency-Key`. Roles are enforced upstream.
 */
export const CRM_AUTHORING_COMMAND_POST_PATHS: readonly RegExp[] = [
  /^\/v2\/commands\/create-person$/,
  /^\/v2\/commands\/update-person$/,
  /^\/v2\/commands\/archive-person$/,
  /^\/v2\/commands\/restore-person$/,
  /^\/v2\/commands\/merge-people$/,
  /^\/v2\/commands\/add-contact-point$/,
  /^\/v2\/commands\/update-contact-point$/,
  /^\/v2\/commands\/deactivate-contact-point$/,
  /^\/v2\/commands\/link-person-organization$/,
  /^\/v2\/commands\/unlink-person-organization$/,
  /^\/v2\/commands\/register-organization$/,
  /^\/v2\/commands\/update-organization$/,
  /^\/v2\/commands\/archive-organization$/,
  /^\/v2\/commands\/restore-organization$/,
  /^\/v2\/commands\/confirm-organization-record$/,
  /^\/v2\/commands\/add-organization-identifier$/,
  /^\/v2\/commands\/remove-organization-identifier$/,
  /^\/v2\/commands\/add-organization-domain$/,
  /^\/v2\/commands\/remove-organization-domain$/,
  /^\/v2\/commands\/restore-organization-domain$/,
  /^\/v2\/commands\/add-organization-classification$/,
  /^\/v2\/commands\/remove-organization-classification$/,
  /^\/v2\/commands\/link-organization-product-line$/,
  /^\/v2\/commands\/unlink-organization-product-line$/,
  /^\/v2\/commands\/confirm-supplier-candidate$/,
  /^\/v2\/commands\/reject-supplier-candidate$/,
  /^\/v2\/commands\/add-note$/,
  /^\/v2\/commands\/revise-note$/,
  /^\/v2\/commands\/archive-note$/,
];

/** 64 KiB: enough for a CRM authoring body (names, notes, identifiers). */
export const CRM_AUTHORING_MAX_BYTES = 65_536;

export function isAllowedCrmAuthoringCommandPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return CRM_AUTHORING_COMMAND_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

/**
 * Catalog commands (JSON): create and update a product, confirm its content, record a supplier
 * cost, set supplier terms and cost parameters, record an FX rate, review a document line and
 * update a product image's metadata. Nine exact paths under `/v2/commands/`. Every request here
 * must pass `catalogCommandRefusal` in index.ts: an allowed `Origin`, no cross-site
 * `Sec-Fetch-Site`, a JSON body within `CATALOG_COMMAND_MAX_BYTES` and a well-formed
 * `Idempotency-Key`. Roles are enforced upstream.
 */
export const CATALOG_COMMAND_POST_PATHS: readonly RegExp[] = [
  /^\/v2\/commands\/create-product$/,
  /^\/v2\/commands\/update-product$/,
  /^\/v2\/commands\/confirm-product-content$/,
  /^\/v2\/commands\/record-supplier-cost$/,
  /^\/v2\/commands\/set-supplier-terms$/,
  /^\/v2\/commands\/set-cost-parameter$/,
  /^\/v2\/commands\/record-fx-rate$/,
  /^\/v2\/commands\/update-product-image$/,
  /^\/v2\/commands\/review-document-line$/,
];

/** 64 KiB: enough for any catalog JSON body. */
export const CATALOG_COMMAND_MAX_BYTES = 65_536;

/**
 * The one multipart POST: a product image. The API caps the file at 8 MiB and refuses oversize
 * bodies itself; the Worker's limit is the file plus 64 KiB of form overhead (defence in depth).
 */
export const CATALOG_UPLOAD_POST_PATHS: readonly RegExp[] = [/^\/v2\/commands\/add-product-image$/];

export const CATALOG_UPLOAD_MAX_BYTES = 8_388_608 + 65_536;

/** The body limit for one catalog command path: the upload's, or the JSON commands'. */
export function catalogCommandMaxBytes(pathname: string): number {
  return CATALOG_UPLOAD_POST_PATHS.some((pattern) => pattern.test(pathname.split("?")[0]))
    ? CATALOG_UPLOAD_MAX_BYTES
    : CATALOG_COMMAND_MAX_BYTES;
}

export function isAllowedCatalogCommandPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return CATALOG_COMMAND_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

export function isAllowedCatalogUploadPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return CATALOG_UPLOAD_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

/**
 * Sign-out. Clears the session cookie upstream (and records the logout); listed apart from
 * the commercial commands so it can never inherit their headers or be mistaken for one.
 */
export const AUTH_LOGOUT_POST_PATH_RE = /^\/auth\/logout$/;

/**
 * Profile selection behind a shared Workspace sign-in: choose a profile with its PIN, or
 * return to the profile screen. Upstream the API verifies the PIN and binds the operator into
 * the session; the browser names only a profile id and a PIN. Two exact paths -- never
 * `/auth/profile/.+`, and never the local-only `/auth/dev/*` shortcut.
 */
export const AUTH_PROFILE_POST_PATHS: readonly RegExp[] = [
  /^\/auth\/profile\/select$/,
  /^\/auth\/profile\/clear$/,
];

/** A profile body is a UUID and a PIN: well under a kilobyte. */
export const AUTH_PROFILE_MAX_BYTES = 1_024;

export function isAllowedAuthProfilePostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return AUTH_PROFILE_POST_PATHS.some((pattern) => pattern.test(pathOnly));
}

export function isAllowedAuthPostPath(pathname: string): boolean {
  const pathOnly = pathname.split("?")[0];
  return AUTH_LOGOUT_POST_PATH_RE.test(pathOnly) || isAllowedAuthProfilePostPath(pathOnly);
}

export function isAllowedPostPath(pathname: string): boolean {
  return (
    isAllowedMarketingCommandPostPath(pathname) ||
    isAllowedCrmAuthoringCommandPostPath(pathname) ||
    isAllowedMailRulesCommandPostPath(pathname) ||
    isAllowedCaseCommandPostPath(pathname) ||
    isAllowedCatalogCommandPostPath(pathname) ||
    isAllowedCatalogUploadPostPath(pathname) ||
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
