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
  //  - crmAuthoringApi.ts's POST to the 28 CRM authoring commands: freeform create/update/archive
  //    of person/organization/contact_point, affiliations, product lines, supplier candidates,
  //    and notes. All go through the API's Deciding role check (sales or admin).
  // No other dashboard source file may issue POST/PUT/PATCH/DELETE.
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

  it("allows only the logout POST, the campaign-command POST, and the CRM authoring POST", () => {
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

  it("names no V2 command outside the campaign client and the CRM authoring client", () => {
    const hits = entries
      .filter(([path, text]) => path !== CAMPAIGN_DRAFT_FILE && path !== CRM_AUTHORING_FILE && /\/v2\/commands\//.test(text))
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
  const withoutSanctionedTestSend = (text: string) =>
    text.replace(/\/v2\/commands\/send-campaign-test(?![\w-])/g, "");

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
});
