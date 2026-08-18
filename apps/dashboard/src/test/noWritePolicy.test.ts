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

  // The dashboard is otherwise strictly read-only. Exactly TWO narrowly
  // reviewed mutations are sanctioned, both in institutionIntel/adapter.ts:
  //
  // 1. annex-bundle PREVIEW -- bounded ZIP validation/extraction only;
  //    never persisted or published.
  // 2. annex-bundle IMPORT -- the explicit operator action that persists
  //    validated structured tender evidence only.
  //
  // Neither route authorizes SQLite/Postgres commercial writes, Gmail,
  // contact changes, or outreach. Every other POST/PUT/PATCH/DELETE remains
  // forbidden. The policy checks the exact route literals as well as the
  // exact method inventory so increasing the count cannot silently
  // grandfather in an unrelated mutation.
  const SANCTIONED_MUTATION_FILE = "../api/institutionIntel/adapter.ts";
  const SANCTIONED_MUTATION_ROUTES = [
    "`/operator/procurement/tenders/${encodeURIComponent(tenderCode)}/annex-bundle/preview`",
    "`/operator/procurement/tenders/${encodeURIComponent(tenderCode)}/annex-bundle/import`",
  ] as const;

  it("allows only the two explicit annex-bundle POST mutations", () => {
    const hits: string[] = [];

    for (const [path, text] of entries) {
      const isSanctioned = path === SANCTIONED_MUTATION_FILE;

      if (!isSanctioned && (MUTATION_METHOD.test(text) || FORBIDDEN_FETCH.test(text))) {
        hits.push(path);
        continue;
      }

      if (!isSanctioned) {
        continue;
      }

      const methods = [
        ...text.matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi),
      ].map((match) => match[1].toUpperCase());

      if (
        methods.length !== SANCTIONED_MUTATION_ROUTES.length ||
        methods.some((method) => method !== "POST")
      ) {
        hits.push(
          `${path} (expected exactly ${SANCTIONED_MUTATION_ROUTES.length} POST mutations, found ${methods.join(", ") || "none"})`,
        );
      }

      for (const route of SANCTIONED_MUTATION_ROUTES) {
        const occurrences = text.split(route).length - 1;
        if (occurrences !== 1) {
          hits.push(
            `${path} (expected sanctioned route exactly once: ${route}; found ${occurrences})`,
          );
        }
      }
    }

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

  it("active src does not import parked legacy modules", () => {
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
        path.includes("TodayPage.tsx") ||
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
