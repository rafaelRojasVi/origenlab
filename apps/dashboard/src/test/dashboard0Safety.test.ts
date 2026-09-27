import { describe, expect, it } from "vitest";

const appSource = import.meta.glob("../App.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
})["../App.tsx"] as string;

const operatorClientSource = import.meta.glob("../api/operatorClient.ts", {
  query: "?raw",
  import: "default",
  eager: true,
})["../api/operatorClient.ts"] as string;

const dashboardAppSource = import.meta.glob("../pages/DashboardApp.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
})["../pages/DashboardApp.tsx"] as string;

const commercialMountedSources = Object.entries(
  import.meta.glob(
    "../{pages,crm,components}/**/*.{ts,tsx}",
    {
      query: "?raw",
      import: "default",
      eager: true,
    },
  ),
)
  .filter(([path]) => !path.includes(".test."))
  .map(([, src]) => src as string);

const viteConfigSource = import.meta.glob("../../vite.config.ts", {
  query: "?raw",
  import: "default",
  eager: true,
})["../../vite.config.ts"] as string;

const viteEnvSource = import.meta.glob("../vite-env.d.ts", {
  query: "?raw",
  import: "default",
  eager: true,
})["../vite-env.d.ts"] as string;

const activeApiClientSources = Object.entries(
  import.meta.glob("../{api/operatorClient,api/authClient,crm/crmApi}.ts", {
    query: "?raw",
    import: "default",
    eager: true,
  }),
).map(([, src]) => src as string);

// A client genuinely delegates credentialed JSON GET to operatorClient's shared
// fetchJsonGet only if it both imports that specific named export from operatorClient
// AND actually invokes it -- a bare token match (unused import, comment, unrelated text)
// is not proof of delegation.
const IMPORTS_SHARED_FETCH_JSON_GET =
  /import\s*{[^}]*\bfetchJsonGet\b[^}]*}\s*from\s*["']\.\.?\/(?:api\/)?operatorClient["']/;
const CALLS_SHARED_FETCH_JSON_GET = /\bfetchJsonGet(<[^>]*>)?\(/;

function delegatesToSharedFetchJsonGet(source: string): boolean {
  return IMPORTS_SHARED_FETCH_JSON_GET.test(source) && CALLS_SHARED_FETCH_JSON_GET.test(source);
}

const DASHBOARD_V1_API_PATHS = [
  "/health",
  "/operator/status",
  "/operator/automation-status",
  "/cases/warm",
  "/opportunities/commercial",
];

const LEGACY_API_PATH_FRAGMENTS = [
  "/dashboard",
  "/classification",
  "/commercial/purchase",
  '"/contacts"',
  "/organizations",
  "/outbound",
  "/meta/dashboard",
];

const LEGACY_PANEL_IMPORTS = [
  "ComprasTab",
  "ClassificationSection",
  "TabNav",
  "KpiCards",
  "OrganizationsSection",
  "ReadinessPanel",
  "HowToReadPanel",
  "PurchaseSignalsSection",
  "ConfirmedPurchaseEventsSection",
  "SyncWatermark",
];

const FORBIDDEN_UI_PATTERNS = [
  /\bbody_preview\b/,
  /\bemail_body\b/,
  /\bsource_path\b/,
  /\bsqlite_path\b/,
  /encodeURIComponent\([^)]*subject/i,
  /mailto:[^"']*\?subject=/i,
  /mailto:[^"']*&body=/i,
];

describe("Dashboard-2 safety (mounted Today)", () => {
  it("App.tsx gates DashboardApp behind production host allowlist", () => {
    expect(appSource).toContain("isDashboardHostAllowed");
    expect(appSource).toContain("PrivateDashboardPlaceholder");
  });

  it("App.tsx mounts DashboardApp only (no legacy commercial panels)", () => {
    expect(appSource).toContain("DashboardApp");
    for (const symbol of LEGACY_PANEL_IMPORTS) {
      expect(appSource, `App.tsx must not import or mount ${symbol}`).not.toContain(symbol);
    }
    expect(appSource).not.toMatch(/api\/client/);
    expect(appSource).not.toMatch(/\/legacy\//);
  });

  it("active runtime does not import legacy dashboard paths", () => {
    const activeSources = [appSource, dashboardAppSource, operatorClientSource].join(
      "\n",
    );
    expect(activeSources).not.toMatch(/\/legacy\//);
    expect(activeSources).not.toMatch(/legacy\/api\/client/);
  });

  it("operatorClient calls only apps/api Dashboard routes", () => {
    const paths = [...operatorClientSource.matchAll(/operatorApiUrl\(\s*["']([^"']+)["']/g)].map(
      (m) => m[1],
    );
    expect(paths.sort()).toEqual(DASHBOARD_V1_API_PATHS.sort());
    for (const legacy of LEGACY_API_PATH_FRAGMENTS) {
      expect(operatorClientSource, `legacy route ${legacy}`).not.toContain(legacy);
    }
  });

  it("operatorClient uses GET /contacts/{email} with encoded path only", () => {
    expect(operatorClientSource).toContain("fetchContactProfile");
    expect(operatorClientSource).toMatch(/encodeURIComponent/);
    expect(operatorClientSource).toMatch(/\/contacts\/\$\{encodeURIComponent/);
    expect(operatorClientSource).not.toMatch(/operatorApiUrl\(\s*["']\/contacts["']/);
  });

  it("commercial opportunity detail uses encoded GET path only", () => {
    expect(operatorClientSource).toContain("fetchCommercialOpportunityDetail");
    expect(operatorClientSource).toContain("commercialOpportunityDetailPath");
    expect(operatorClientSource).toMatch(
      /\/opportunities\/commercial\/\$\{encodeURIComponent\(opportunityId\)\}/,
    );
    expect(operatorClientSource).not.toMatch(
      /method:\s*["'](POST|PUT|PATCH|DELETE)["']/i,
    );
  });

  it("operatorClient uses GET fetch only", () => {
    expect(operatorClientSource).toMatch(/method:\s*["']GET["']/);
    expect(operatorClientSource).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/i);
  });

  it("operatorClient requires env in production (no localhost fallback)", () => {
    expect(operatorClientSource).toMatch(/MODE\s*===\s*["']production["']/);
    expect(operatorClientSource).toContain("OperatorApiConfigError");
    expect(operatorClientSource).not.toMatch(/DEFAULT_API_BASE|127\.0\.0\.1:8001/);
  });

  it("mounted commercial UI avoids sensitive fields and mailto prefills", () => {
    const blob = commercialMountedSources.join("\n");
    for (const pattern of FORBIDDEN_UI_PATTERNS) {
      expect(blob, pattern.toString()).not.toMatch(pattern);
    }
    expect(operatorClientSource).toContain("parseWarmCasesResponse");
  });

  it("DashboardApp mounts only the CRM shell behind the sign-in gate", () => {
    expect(dashboardAppSource).toContain("AuthGate");
    expect(dashboardAppSource).toContain("CrmApp");
    expect(dashboardAppSource).not.toMatch(/LegacyFrame|renderLegacy|DashboardDataProvider/);
  });

  it("pre-v1 legacy dashboard tree has been removed", () => {
    const legacyFiles = import.meta.glob("../legacy/**/*", {
      eager: true,
    });
    expect(Object.keys(legacyFiles)).toEqual([]);
  });

  it("vite dev proxy exposes Dashboard v1 API routes only", () => {
    expect(viteConfigSource).toMatch(/["']\/health["']/);
    expect(viteConfigSource).toMatch(/["']\/operator["']/);
    expect(viteConfigSource).toMatch(/["']\/cases["']/);
    expect(viteConfigSource).toMatch(/["']\/opportunities["']/);
    expect(viteConfigSource).toMatch(/["']\/contacts["']/);
    expect(viteConfigSource).toMatch(/["']\/mirror["']/);
    expect(viteConfigSource).not.toMatch(/["']\/dashboard["']/);
    expect(viteConfigSource).not.toMatch(/["']\/classification["']/);
    expect(viteConfigSource).not.toMatch(/["']\/commercial["']/);
  });

  it("commercial deals UI does not reference purchase-events mirror", () => {
    const blob = [dashboardAppSource, commercialMountedSources.join("\n")].join("\n");
    expect(blob).not.toMatch(/\/mirror\/commercial\/purchase-events/);
    expect(blob).not.toMatch(/fetchPurchase|purchase-events["']/);
  });
});

describe("Production API auth gap (docs/tests guard)", () => {
  it("active API clients send credentials include for Cloudflare Access cookies", () => {
    // A client either sends `credentials: "include"` directly, or genuinely delegates to
    // operatorClient's shared fetchJsonGet (itself asserted to send it, above/below).
    const sendsCredentialsInclude = /credentials:\s*["']include["']/;
    for (const source of activeApiClientSources) {
      expect(
        sendsCredentialsInclude.test(source) || delegatesToSharedFetchJsonGet(source),
        "credentials include (directly or via shared fetchJsonGet)",
      ).toBe(true);
    }
  });

  it("active API clients do not inject Authorization or X-OriginLab-API-Key headers", () => {
    const blob = activeApiClientSources.join("\n");
    expect(blob).not.toMatch(/Authorization\s*:/);
    expect(blob).not.toMatch(/X-OriginLab-API-Key/);
    expect(blob).not.toMatch(/Bearer\s+/);
    expect(blob).not.toMatch(/import\.meta\.env\.VITE_[A-Z0-9_]*TOKEN/i);
    expect(blob).not.toMatch(/import\.meta\.env\.VITE_[A-Z0-9_]*AUTH/i);
  });

  it("vite-env.d.ts does not declare VITE auth token env vars", () => {
    expect(viteEnvSource).toContain("VITE_ORIGENLAB_API_BASE_URL");
    expect(viteEnvSource).not.toMatch(/VITE_[A-Z0-9_]*(AUTH|TOKEN)/);
  });

  it("dashboard safety doc exists and forbids VITE auth tokens", () => {
    const docSource = import.meta.glob("../../docs/PRODUCTION_API_AUTH.md", {
      query: "?raw",
      import: "default",
      eager: true,
    })["../../docs/PRODUCTION_API_AUTH.md"] as string;
    expect(docSource).toContain("credentials: \"include\"");
    expect(docSource).toContain("VITE_ORIGENLAB_API_AUTH_TOKEN");
    expect(docSource).toMatch(/401/);
    expect(docSource).toMatch(/proxy|BFF|JWT|dashboard-proxy/i);
  });
});
