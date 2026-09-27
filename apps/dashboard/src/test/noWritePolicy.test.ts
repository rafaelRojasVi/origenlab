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

  // The dashboard records no commercial decision. Two modules may issue a mutating request:
  //  - authClient.ts's POST to `/auth/logout`, which clears the session cookie upstream;
  //  - marketingApi.ts's POST to exactly the three campaign commands: the two *draft* commands,
  //    which write email copy to a draft `outbound.campaign` row, and the audience *freeze*,
  //    which writes an immutable recipient snapshot. None approves or sends anything.
  // No other dashboard source file may issue POST/PUT/PATCH/DELETE.
  const AUTH_LOGOUT_FILE = "../api/authClient.ts";
  const CAMPAIGN_DRAFT_FILE = "../crm/marketing/marketingApi.ts";
  const CAMPAIGN_DRAFT_PATHS = [
    "/v2/commands/create-campaign-draft",
    "/v2/commands/save-campaign-draft",
    "/v2/commands/freeze-campaign-audience",
  ];

  it("allows only the logout POST and the campaign-command POST", () => {
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
    }
    expect(hits).toEqual([]);
  });

  it("names no V2 command outside the campaign client", () => {
    const hits = entries
      .filter(([path, text]) => path !== CAMPAIGN_DRAFT_FILE && /\/v2\/commands\//.test(text))
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

  it("has no send capability: no Gmail client, no send command, no Send button", () => {
    const hits = entries
      .filter(([, text]) => /gmail\.googleapis|googleapis\.com\/gmail|\/v2\/commands\/(?:send|approve|activate|dispatch)|>\s*Enviar(?:\s+campaña)?\s*</i.test(text))
      .map(([path]) => path);
    expect(hits).toEqual([]);
  });
});
