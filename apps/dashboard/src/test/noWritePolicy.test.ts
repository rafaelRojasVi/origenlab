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

  // The dashboard writes nothing. The one mutating request is authClient.ts's POST to
  // `/auth/logout`, which clears the session cookie upstream and writes no commercial or CRM
  // state. No other dashboard source file may issue POST/PUT/PATCH/DELETE.
  const AUTH_LOGOUT_FILE = "../api/authClient.ts";

  it("allows only the logout POST", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      if (!MUTATION_METHOD.test(text) && !FORBIDDEN_FETCH.test(text)) continue;
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
});
