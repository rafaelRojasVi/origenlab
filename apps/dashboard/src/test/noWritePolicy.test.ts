import { describe, expect, it } from "vitest";

const sourceModules = import.meta.glob(["../**/*.ts", "../**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const MUTATION_METHOD = /method:\s*["'](POST|PUT|PATCH|DELETE)["']/i;
const FORBIDDEN_IMPORT = /(?:from\s+["']|import\s+["'])[^"']*(?:email[-_]pipeline|origenlab_email|psycopg|sqlite3|better-sqlite)/i;
const FORBIDDEN_FETCH = /\bfetch\s*\([^)]*,\s*\{[^}]*method:\s*["'](POST|PUT|PATCH|DELETE)["']/is;
const LEGACY_CLIENT_IMPORT = /from\s+["'][^"']*\/api\/client["']/;
const LEGACY_API_ROUTE =
  /operatorApiUrl\(\s*["']\/(dashboard|classification|commercial|contacts["']|organizations|outbound|meta)/;

function isAppSource(path: string): boolean {
  if (path.includes("/test/") || path.includes("/legacy/")) {
    return false;
  }
  if (path.endsWith(".test.ts") || path.endsWith(".test.tsx")) {
    return false;
  }
  return true;
}

describe("dashboard read-only policy", () => {
  const entries = Object.entries(sourceModules).filter(([path]) => isAppSource(path));

  it("scans dashboard src (not only tests)", () => {
    expect(entries.length).toBeGreaterThan(5);
  });

  // The dashboard records no commercial decision. Three modules may issue a mutating request:
  //  - authClient.ts's one POST site, to exactly four sign-in paths: `/auth/logout` (clears the
  //    session), `/auth/profile/select` and `/auth/profile/clear` (choose or leave an operator
  //    profile behind a shared Workspace sign-in; the API verifies the PIN), and the API's
  //    local-only `/auth/dev/principal-session`. None writes commercial state;
  //  - marketingApi.ts's POST to exactly eight marketing commands: the two *draft* commands,
  //    which write email copy to a draft `outbound.campaign` row, the audience *freeze*, which
  //    writes an immutable recipient snapshot, *planning*, which notes an intended send day on
  //    an unsent campaign, the two W10 *review* decisions on a «BAJA» held for review (confirm
  //    it as a permanent unsubscribe; an admin dismisses a false positive), and the admin-only
  //    *block* / *unblock*, which place or lift a campaign safety block that only refuses. None
  //    approves, schedules or sends anything, and none lifts a confirmed unsubscribe.
  //  - crmAuthoringApi.ts's POST to the 29 CRM authoring commands: freeform create/update/archive
  //    of person/organization/contact_point, affiliations, product lines, supplier candidates,
  //    and notes. All go through the API's Deciding role check (sales or admin).
  //  - mailRules.ts's POST to the two admin-only email → cases commands: apply the rules'
  //    automatic actions (re-planned upstream; the browser sends no action) and undo one, and the
  //    mail-triage verdict (review-triage: approve, correct or reject a suggestion; sales or admin).
  //  - caseCommands.ts's POST to exactly six commercial-case commands and the three W11 task
  //    commands (create/complete/cancel-task, «En pausa hasta…» / «Retomar ahora»), the case drawer's
  //    «Cambiar etapa» (advance-case-stage), «Marcar ganada» (record-case-won), «Elegir revisión
  //    vigente» (resolve-current-revision) and «Registrar cotización» / «Nueva revisión»
  //    (record-case-quotation), plus audited institution assignment/review commands.
  //    Sales or admin upstream. Cross-thread quote evidence can be manually linked after
  //    review. «Abrir caso» on a «Correos sin caso» row opens a case from that email
  //    (open-commercial-case, owner decision 2026-10-10); recording an interest remains outside
  //    the browser.
  //  - caseDocuments.ts's POST to the three purchase-order commands (owner request 2026-10-12):
  //    attach a case's OC from the computer (multipart) or from a case email, and remove one.
  //    Sales or admin upstream; the file is stored privately, never served from here.
  //  - crm/quotes/quoteApi.ts's POST to the five quote commands (create-quote, save-quote-draft,
  //    approve-quote-revision, void-quote-revision, create-quote-revision; sales or admin upstream,
  //    each with an Idempotency-Key) and to `/v2/quotes/preview`, which prices a draft and writes
  //    nothing. The browser never computes a price: every figure comes back from the API.
  //    It also POSTs set-cost-parameter for «Preferencias» of the Cotizador (sales or admin; the
  //    numbering seed admin only), with an Idempotency-Key, a reason and the current row seen: a
  //    company default, which prices and sends nothing.
  // No other dashboard source file may issue POST/PUT/PATCH/DELETE.
  const MAIL_RULES_FILE = "../crm/mailRules.ts";
  const MAIL_RULES_PATHS = [
    "/v2/commands/apply-mail-rules",
    "/v2/commands/undo-mail-rule-action",
    "/v2/commands/set-auto-mail-rules",
    "/v2/commands/review-triage",
  ];
  const CASE_COMMANDS_FILE = "../crm/caseCommands.ts";
  const CASE_COMMAND_PATHS = [
    "/v2/commands/advance-case-stage",
    "/v2/commands/add-case-organization",
    "/v2/commands/set-case-organization-role",
    "/v2/commands/record-case-won",
    "/v2/commands/resolve-current-revision",
    "/v2/commands/record-case-quotation",
    "/v2/commands/link-case-evidence",
    "/v2/commands/create-task",
    "/v2/commands/complete-task",
    "/v2/commands/cancel-task",
    "/v2/commands/open-commercial-case",
    // «Completar con la cotización del correo» (2026-10-13): a sent quote → case in «Enviada».
    "/v2/commands/complete-case-from-sent-quote",
    // Forwarded requests (2026-10-14): «Crear solicitud» and «Descartar» in Hoy.
    "/v2/commands/create-request-from-forward",
    "/v2/commands/dismiss-forwarded-request",
    // «Quitar» on Hoy (2026-10-14): undo one action the automatic email rules took.
    "/v2/commands/remove-automatic-action",
  ];
  const CASE_DOCUMENTS_FILE = "../crm/caseDocuments.ts";
  const CASE_DOCUMENT_PATHS = [
    "/v2/commands/attach-case-document",
    "/v2/commands/attach-case-document-from-email",
    "/v2/commands/remove-case-document",
  ];
  const QUOTE_API_FILE = "../crm/quotes/quoteApi.ts";
  const QUOTE_API_PATHS = [
    "/v2/commands/create-quote",
    "/v2/commands/save-quote-draft",
    "/v2/commands/approve-quote-revision",
    "/v2/commands/void-quote-revision",
    "/v2/commands/create-quote-revision",
    "/v2/commands/set-cost-parameter",
  ];
  const QUOTE_PREVIEW_PATH = "/v2/quotes/preview";
  // «Licitaciones» (2026-10-19): tenderApi.ts POSTs «Nueva licitación», the bidder commands from
  // «Preferencias» (save the profile, add or retire a standard document) and a tender page's commands
  // (its estado, «Subir anexos», «Preparar oferta» / «Llenar formularios», «Crear oferta económica»);
  // sales or admin upstream, each with an Idempotency-Key. Nothing is sent to Mercado Público or
  // anyone else from here.
  const TENDER_API_FILE = "../crm/tenders/tenderApi.ts";
  const TENDER_API_PATHS = [
    "/v2/commands/open-tender-case",
    "/v2/commands/save-bidder-profile",
    "/v2/commands/add-bidder-document",
    "/v2/commands/retire-bidder-document",
    "/v2/commands/set-tender-status",
    "/v2/commands/attach-tender-annexes",
    "/v2/commands/remove-tender-annex",
    "/v2/commands/prepare-tender-bid",
    "/v2/commands/create-tender-offer-quote",
    "/v2/commands/review-notice",
  ];
  const AUTH_LOGOUT_FILE = "../api/authClient.ts";
  const CAMPAIGN_DRAFT_FILE = "../crm/marketing/marketingApi.ts";
  const CRM_AUTHORING_FILE = "../crm/authoring/crmAuthoringApi.ts";
  const CAMPAIGN_DRAFT_PATHS = [
    "/v2/commands/create-campaign-draft",
    "/v2/commands/save-campaign-draft",
    "/v2/commands/freeze-campaign-audience",
    "/v2/commands/set-campaign-planning",
    "/v2/commands/resolve-unsubscribe-review",
    "/v2/commands/dismiss-unsubscribe-review",
    "/v2/commands/confirm-triage-unsubscribe",
    "/v2/commands/block-campaign",
    "/v2/commands/unblock-campaign",
    "/v2/commands/send-campaign-test",
  ];
  const CRM_AUTHORING_PATHS = [
    "/v2/commands/create-person",
    "/v2/commands/update-person",
    "/v2/commands/archive-person",
    "/v2/commands/restore-person",
    "/v2/commands/merge-people",
    "/v2/commands/add-contact-point",
    "/v2/commands/update-contact-point",
    "/v2/commands/deactivate-contact-point",
    "/v2/commands/link-person-organization",
    "/v2/commands/unlink-person-organization",
    "/v2/commands/register-organization",
    "/v2/commands/update-organization",
    "/v2/commands/archive-organization",
    "/v2/commands/restore-organization",
    "/v2/commands/confirm-organization-record",
    "/v2/commands/add-organization-identifier",
    "/v2/commands/remove-organization-identifier",
    "/v2/commands/add-organization-domain",
    "/v2/commands/remove-organization-domain",
    "/v2/commands/restore-organization-domain",
    "/v2/commands/add-organization-classification",
    "/v2/commands/remove-organization-classification",
    "/v2/commands/link-organization-product-line",
    "/v2/commands/unlink-organization-product-line",
    "/v2/commands/confirm-supplier-candidate",
    "/v2/commands/reject-supplier-candidate",
    "/v2/commands/add-note",
    "/v2/commands/revise-note",
    "/v2/commands/archive-note",
  ];

  it("allows only the logout POST, the campaign-command POST, the CRM authoring POST, the email-rules POST and the case-command POST", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      if (!MUTATION_METHOD.test(text) && !FORBIDDEN_FETCH.test(text)) continue;
      if (path === CAMPAIGN_DRAFT_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...CAMPAIGN_DRAFT_PATHS].sort())) {
          hits.push(`${path} (may target only ${CAMPAIGN_DRAFT_PATHS.join(" and ")}, found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path === CRM_AUTHORING_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...CRM_AUTHORING_PATHS].sort())) {
          hits.push(`${path} (CRM authoring paths mismatch: found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path === MAIL_RULES_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...MAIL_RULES_PATHS].sort())) {
          hits.push(`${path} (email-rules paths mismatch: found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path === CASE_COMMANDS_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...CASE_COMMAND_PATHS].sort())) {
          hits.push(`${path} (case command paths mismatch: found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path === QUOTE_API_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...QUOTE_API_PATHS].sort())) {
          hits.push(`${path} (quote command paths mismatch: found ${commandPaths.join(", ")})`);
        }
        const quotePosts = [...text.matchAll(/["'](\/v2\/quotes\/(?:preview|numbering))["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(quotePosts) !== JSON.stringify(["/v2/quotes/numbering", QUOTE_PREVIEW_PATH])) {
          hits.push(`${path} (quote paths mismatch: found ${quotePosts.join(", ")})`);
        }
        continue;
      }
      if (path === TENDER_API_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...TENDER_API_PATHS].sort())) {
          hits.push(`${path} (tender command paths mismatch: found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path === CASE_DOCUMENTS_FILE) {
        const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
        if (methods.length !== 1 || methods[0] !== "POST") {
          hits.push(`${path} (expected exactly one POST site, found ${methods.join(", ") || "none"})`);
        }
        const commandPaths = [...text.matchAll(/["'](\/v2\/commands\/[^"']*)["']/g)].map((m) => m[1]).sort();
        if (JSON.stringify(commandPaths) !== JSON.stringify([...CASE_DOCUMENT_PATHS].sort())) {
          hits.push(`${path} (case document paths mismatch: found ${commandPaths.join(", ")})`);
        }
        continue;
      }
      if (path !== AUTH_LOGOUT_FILE) {
        hits.push(`${path} (unsanctioned mutation module)`);
        continue;
      }
      const methods = [...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi)].map((m) => m[1].toUpperCase());
      if (methods.length !== 1 || methods[0] !== "POST") {
        hits.push(`${path} (expected exactly one POST, the logout, found ${methods.join(", ") || "none"})`);
      }
      if (!text.includes('export const AUTH_LOGOUT_PATH = "/auth/logout";')) {
        hits.push(`${path} (logout POST must target /auth/logout)`);
      }
      const authPaths = [...text.matchAll(/export const AUTH_[A-Z_]+_PATH = "(\/auth\/[^"]*)";/g)].map((m) => m[1]).sort();
      const expectedAuthPaths = [
        "/auth/dev/principal-session",
        "/auth/google/login",
        "/auth/logout",
        "/auth/profile/clear",
        "/auth/profile/select",
        "/auth/profiles",
        "/auth/session",
      ];
      if (JSON.stringify(authPaths) !== JSON.stringify(expectedAuthPaths)) {
        hits.push(`${path} (sign-in paths changed: ${authPaths.join(", ")})`);
      }
      const postList = /const AUTH_POST_PATHS = \[([^\]]*)\]/.exec(text)?.[1] ?? "";
      const postNames = [...postList.matchAll(/AUTH_[A-Z_]+_PATH/g)].map((m) => m[0]).sort();
      if (JSON.stringify(postNames) !== JSON.stringify(["AUTH_DEV_PRINCIPAL_PATH", "AUTH_LOGOUT_PATH", "AUTH_PROFILE_CLEAR_PATH", "AUTH_PROFILE_SELECT_PATH"])) {
        hits.push(`${path} (may POST only to logout, profile select/clear and the dev shortcut, found ${postNames.join(", ")})`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("names no V2 command outside the campaign, CRM authoring, email-rules, case-command and quote clients", () => {
    const owners = new Set([CAMPAIGN_DRAFT_FILE, CRM_AUTHORING_FILE, MAIL_RULES_FILE, CASE_COMMANDS_FILE, CASE_DOCUMENTS_FILE, QUOTE_API_FILE, TENDER_API_FILE]);
    const hits = entries
      .filter(([path, text]) => !owners.has(path) && /\/v2\/commands\//.test(text))
      .map(([path]) => path);
    expect(hits).toEqual([]);
  });

  it("never injects the trusted operator identity header from the browser", () => {
    const hits = entries.filter(([, text]) => /X-OriginLab-Operator-Email/i.test(text)).map(([path]) => path);
    expect(hits).toEqual([]);
  });

  it("does not import pipeline or database drivers", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      if (FORBIDDEN_IMPORT.test(text)) {
        hits.push(path);
      }
    }
    expect(hits).toEqual([]);
  });

  it("active src does not import legacy dashboard modules", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      if (/from\s+["'][^"']*\/legacy\//.test(text) || /import\s+["'][^"']*\/legacy\//.test(text)) {
        hits.push(path);
      }
    }
    expect(hits).toEqual([]);
  });

  it("mounted runtime does not import legacy api/client or legacy routes", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      const isMounted =
        path.includes("App.tsx") ||
        path.includes("operatorClient.ts") ||
        path.includes("/commercial/") ||
        path.includes("ContactProfilePanel") ||
        path.includes("contactParse") ||
        path.includes("/operator/");
      if (!isMounted) {
        continue;
      }
      if (LEGACY_CLIENT_IMPORT.test(text) || LEGACY_API_ROUTE.test(text)) {
        hits.push(path);
      }
    }
    expect(hits).toEqual([]);
  });

  // Owner decision 2026-10-04: «Enviar prueba» (/v2/commands/send-campaign-test). Anything else
  // that sends, approves, activates or dispatches — or a plain «Enviar» button — is still refused.
  const SEND_CAPABILITY =
    /gmail\.googleapis|googleapis\.com\/gmail|\/v2\/commands\/(?:send|approve|activate|dispatch)|>\s*Enviar(?:\s+campaña)?\s*</i;
  // Owner decision 2026-10-16 (Cotizador): «Aprobar» freezes a quote revision's prices
  // (/v2/commands/approve-quote-revision). It sends nothing: the PDF and the email are separate.
  const withoutSanctionedTestSend = (text: string) =>
    text
      .replace(/\/v2\/commands\/send-campaign-test(?![\w-])/g, "")
      .replace(/\/v2\/commands\/approve-quote-revision(?![\w-])/g, "");

  it("has no send capability except the admin-only test send of one campaign to one address", () => {
    const hits = entries
      .map(([path, text]) => [path, withoutSanctionedTestSend(text)] as const)
      .filter(([, text]) => SEND_CAPABILITY.test(text))
      .map(([path]) => path);
    expect(hits).toEqual([]);
  });

  it("carves out exactly the test-send path, never a longer one that starts with it", () => {
    for (const probe of [
      "/v2/commands/send-campaign-test-all",
      "/v2/commands/send-campaign-test-and-campaign",
      "/v2/commands/send-campaign-test_all",
      "/v2/commands/send-campaign-test2",
    ]) {
      expect(SEND_CAPABILITY.test(withoutSanctionedTestSend(`"${probe}"`)), probe).toBe(true);
    }
    expect(SEND_CAPABILITY.test(withoutSanctionedTestSend(`"/v2/commands/send-campaign-test"`))).toBe(false);
  });

  it("carves out exactly the quote-approval path, never a longer one that starts with it", () => {
    for (const probe of [
      "/v2/commands/approve-quote-revision-and-send",
      "/v2/commands/approve-quote-revision_all",
      "/v2/commands/approve-quote-revisions",
      "/v2/commands/approve-campaign",
    ]) {
      expect(SEND_CAPABILITY.test(withoutSanctionedTestSend(`"${probe}"`)), probe).toBe(true);
    }
    expect(SEND_CAPABILITY.test(withoutSanctionedTestSend(`"/v2/commands/approve-quote-revision"`))).toBe(false);
  });
});
