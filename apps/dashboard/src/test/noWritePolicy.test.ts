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

  // Dashboard mutation authority is intentionally split across exactly three
  // narrow modules:
  //
  // 1. institutionIntel/adapter.ts:
  //    two explicit annex-bundle POST actions (preview/import).
  //
  // 2. commercialOperationsClient.ts:
  //    one shared POST transport used only by the five explicit CRM command
  //    shapes admitted by the production Worker.
  //
  // 3. customerQuoteClient.ts (CRM-Q1 + CRM-Q2):
  //    seven explicit quote POST commands (create quote, retry Drive
  //    workspace provisioning, submit-for-review, request-adjustments,
  //    approve, confirm-send, adopt-drive-folder) admitted by the
  //    production Worker -- the four revision-transition commands share
  //    one literal `method: "POST"` fetch call site (transitionCustomerQuote),
  //    so only 4 literal POST occurrences appear in the source text.
  //
  // No other dashboard source file may issue POST/PUT/PATCH/DELETE.

  const ANNEX_MUTATION_FILE =
    "../api/institutionIntel/adapter.ts";

  const COMMERCIAL_MUTATION_FILE =
    "../api/commercialOperationsClient.ts";

  const CUSTOMER_QUOTE_MUTATION_FILE =
    "../api/customerQuoteClient.ts";

  const ANNEX_MUTATION_ROUTES = [
    "`/operator/procurement/tenders/${encodeURIComponent(tenderCode)}/annex-bundle/preview`",
    "`/operator/procurement/tenders/${encodeURIComponent(tenderCode)}/annex-bundle/import`",
  ] as const;

  it("allows only annex and commercial-operations POST mutation modules", () => {
    const hits: string[] = [];

    for (const [path, text] of entries) {
      const hasMutation =
        MUTATION_METHOD.test(text) ||
        FORBIDDEN_FETCH.test(text);

      if (!hasMutation) {
        continue;
      }

      if (
        path !== ANNEX_MUTATION_FILE &&
        path !== COMMERCIAL_MUTATION_FILE &&
        path !== CUSTOMER_QUOTE_MUTATION_FILE
      ) {
        hits.push(
          `${path} (unsanctioned mutation module)`,
        );
        continue;
      }

      const methods = [
        ...text.matchAll(
          /method:\s*["'](POST|PUT|PATCH|DELETE)["']/gi,
        ),
      ].map((match) =>
        match[1].toUpperCase(),
      );

      if (path === ANNEX_MUTATION_FILE) {
        if (
          methods.length !==
            ANNEX_MUTATION_ROUTES.length ||
          methods.some(
            (method) => method !== "POST",
          )
        ) {
          hits.push(
            `${path} (expected exactly ${ANNEX_MUTATION_ROUTES.length} POST mutations, found ${methods.join(", ") || "none"})`,
          );
        }

        for (
          const route of ANNEX_MUTATION_ROUTES
        ) {
          const occurrences =
            text.split(route).length - 1;

          if (occurrences !== 1) {
            hits.push(
              `${path} (expected sanctioned route exactly once: ${route}; found ${occurrences})`,
            );
          }
        }

        continue;
      }

      if (path === CUSTOMER_QUOTE_MUTATION_FILE) {
        if (
          methods.length !== 4 ||
          methods.some(
            (method) => method !== "POST",
          )
        ) {
          hits.push(
            `${path} (expected exactly 4 literal POST fetch call sites -- create, retry-drive-workspace, the shared revision-transition helper, and adopt-drive-folder -- found ${methods.join(", ") || "none"})`,
          );
        }

        const requiredPathBuilders = [
          "salesOpportunityQuotesPath",
          "customerQuoteDriveWorkspacePath",
          "customerQuoteSubmitForReviewPath",
          "customerQuoteRequestAdjustmentsPath",
          "customerQuoteApprovePath",
          "customerQuoteConfirmSendPath",
          "salesOpportunityAdoptDriveFolderPath",
        ];

        for (const builder of requiredPathBuilders) {
          if (!text.includes(builder)) {
            hits.push(`${path} (missing command path builder: ${builder})`);
          }
        }

        if (
          /method:\s*["'](PUT|PATCH|DELETE)["']/i.test(
            text,
          )
        ) {
          hits.push(
            `${path} (PUT/PATCH/DELETE remain forbidden)`,
          );
        }

        if (
          /X-OriginLab-Operator-Email/i.test(text) &&
          !/never supplies X-OriginLab-Operator-Email/i.test(
            text,
          )
        ) {
          hits.push(
            `${path} (browser must not inject trusted operator identity)`,
          );
        }

        continue;
      }

      if (
        methods.length !== 1 ||
        methods[0] !== "POST"
      ) {
        hits.push(
          `${path} (expected one shared POST transport, found ${methods.join(", ") || "none"})`,
        );
      }

      if (
        !text.includes(
          'operatorApiUrl("/operations/activities")',
        )
      ) {
        hits.push(
          `${path} (missing activities command route)`,
        );
      }

      if (
        !text.includes(
          'operatorApiUrl("/operations/tasks")',
        )
      ) {
        hits.push(
          `${path} (missing tasks command route)`,
        );
      }

      if (
        !text.includes(
          "commercialOpportunityOperatorStatePath",
        )
      ) {
        hits.push(
          `${path} (missing opportunity-state command path)`,
        );
      }

      if (
        !text.includes(
          "commercialTaskTransitionPath",
        )
      ) {
        hits.push(
          `${path} (missing task-transition command path)`,
        );
      }

      if (
        !text.includes(
          'action: "complete" | "cancel"',
        )
      ) {
        hits.push(
          `${path} (task transition actions are not narrowly typed)`,
        );
      }

      if (
        /method:\s*["'](PUT|PATCH|DELETE)["']/i.test(
          text,
        )
      ) {
        hits.push(
          `${path} (PUT/PATCH/DELETE remain forbidden)`,
        );
      }

      if (
        /X-OriginLab-Operator-Email/i.test(text) &&
        !/never supplies X-OriginLab-Operator-Email/i.test(
          text,
        )
      ) {
        hits.push(
          `${path} (browser must not inject trusted operator identity)`,
        );
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
