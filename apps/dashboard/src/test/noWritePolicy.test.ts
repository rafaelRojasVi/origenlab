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

  // The dashboard is otherwise strictly read-only. This is the ONE sanctioned
  // exception: the annex-bundle upload PREVIEW
  // (`POST .../tenders/{tender_code}/annex-bundle/preview` in
  // institutionIntel/adapter.ts's `previewTenderAnnexBundle`). It triggers
  // server-side compute (bounded ZIP validation + T1 extraction) but never
  // writes SQLite/Postgres/Gmail and never persists or publishes anything --
  // see TenderAnnexPreview's own safety flags (`published`/`persisted`
  // always false) and apps/api's tender_annex_preview_service.py. Any other
  // mutating-method usage anywhere else in dashboard src remains forbidden,
  // and even this file must contain exactly the one reviewed occurrence --
  // a second one appearing here fails loudly rather than being silently
  // grandfathered in.
  const SANCTIONED_MUTATION_FILE = "../api/institutionIntel/adapter.ts";
  const SANCTIONED_MUTATION_COUNT = 1;

  it("does not use mutating HTTP methods in source, except the one sanctioned annex-bundle preview call", () => {
    const hits: string[] = [];
    for (const [path, text] of entries) {
      const isSanctioned = path === SANCTIONED_MUTATION_FILE;
      if (!isSanctioned && (MUTATION_METHOD.test(text) || FORBIDDEN_FETCH.test(text))) {
        hits.push(path);
      }
      if (isSanctioned) {
        const count = (text.match(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi) ?? []).length;
        if (count !== SANCTIONED_MUTATION_COUNT) {
          hits.push(`${path} (expected exactly ${SANCTIONED_MUTATION_COUNT} sanctioned mutation, found ${count})`);
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
