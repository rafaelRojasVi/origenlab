import { describe, expect, it } from "vitest";

import {
  isAllowedPostPath,
  isAllowedUpstreamPath,
  stripApiPrefix,
} from "../src/allowlist";
import { buildUpstreamUrl } from "../src/proxy";

describe("allowlist", () => {
  const PRODUCTION_SMOKE_PATHS = ["/health"];

  // V1 surfaces with no role model and no redaction upstream. The Worker refuses them so
  // that V2 is the only browser path to CRM, contact and evidence data.
  const V1_BLOCKED_PATHS = [
    "/contacts/a@b.co",
    "/contacts/user%40example.com",
    "/contacts/anything",
    "/mirror/catalog/products",
    "/mirror/leads/summary",
    "/mirror/leads/prospects",
    "/mirror/audits/gmail-interactions",
    "/mirror/commercial/deals",
    "/mirror/x",
    "/operator/status",
    "/operator/automation-status",
    "/operator/procurement/status",
    "/operator/procurement/institutions",
    "/operator/procurement/institutions/test-institution-id",
    "/operator/procurement/queues/current_opportunity",
    "/operator/procurement/queues/historical_prospect",
    "/operator/procurement/queues/contact_gap",
    "/operator/procurement/queues/institution_match_review",
    "/operator/procurement/queues/line_evidence_review",
    "/operator/procurement/queues/retender_review",
    "/operator/procurement/tenders/745712-14-LE26",
    "/operator/procurement/tenders/745712-14-LE26/attachment-navigation",
    "/operator/procurement/tenders/745712-19-lp26",
    "/cases/warm",
    "/opportunities/commercial",
    "/opportunities/commercial/o_0123456789abcdef0123456789abcdef",
    "/operations/work-queue",
    "/operations/sales-opportunities",
    "/operations/customer-quotes",
    "/operations/customer-quotes/drive-pending",
    "/operations/customer-quotes/drive-pending/resolve",
    "/operations/tasks",
    "/operations/activities",
    "/operations/sales-opportunities/promote",
    "/operations/sales-opportunities/manual",
  ];

  it("stripApiPrefix maps /api/* to upstream paths", () => {
    expect(stripApiPrefix("/api/health")).toBe("/health");
    expect(stripApiPrefix("/api/operator/status")).toBe("/operator/status");
    expect(stripApiPrefix("/api/contacts/user%40example.com")).toBe("/contacts/user%40example.com");
    expect(stripApiPrefix("/api/mirror/catalog/products")).toBe("/mirror/catalog/products");
  });

  it("stripApiPrefix rejects paths outside /api", () => {
    expect(stripApiPrefix("/operator/status")).toBeNull();
    expect(stripApiPrefix("/health")).toBeNull();
  });

  it("isAllowedUpstreamPath allows /health and refuses unlisted paths", () => {
    expect(isAllowedUpstreamPath("/health")).toBe(true);
    expect(isAllowedUpstreamPath("/emails")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/send")).toBe(false);
  });

  it.each(PRODUCTION_SMOKE_PATHS)("allows production smoke path %s", (path) => {
    expect(isAllowedUpstreamPath(path)).toBe(true);
    expect(isAllowedUpstreamPath(`${path}?limit=20`)).toBe(true);
  });

  it.each(V1_BLOCKED_PATHS)("refuses the V1 path %s on every method list", (path) => {
    expect(isAllowedUpstreamPath(path)).toBe(false);
    expect(isAllowedUpstreamPath(`${path}?limit=20`)).toBe(false);
    expect(isAllowedPostPath(path)).toBe(false);
  });

  it("allows exactly the CRM workspace reads, GET only", () => {
    for (const path of [
      "/v2/workspace/overview",
      "/v2/workspace/pipeline",
      "/v2/workspace/drive",
      "/v2/workspace/review",
      "/v2/workspace/fx",
      "/v2/workspace/mail-sync",
      "/v2/workspace/mail-quote-numbers",
      "/v2/cockpit/work-queue",
    ]) {
      expect(isAllowedUpstreamPath(path)).toBe(true);
      expect(isAllowedPostPath(path)).toBe(false);
    }
    expect(isAllowedUpstreamPath("/v2/cockpit/work-queue?limit=200")).toBe(true);
  });

  it("keeps every other workspace and cockpit path refused", () => {
    // Built upstream, GET-only and redacting, but not a browser surface: each of these is
    // a separate decision. Neighbours of the allowed paths are refused too.
    const uuid = "96301691-af05-51ea-82e3-05f5fae40837";
    const sha = "a".repeat(64);
    for (const path of [
      "/v2/workspace",
      "/v2/workspace/",
      "/v2/workspace/overview/",
      "/v2/workspace/pipeline/extra",
      `/v2/workspace/pipeline/${uuid}`,
      "/v2/workspace/other",
      "/v2/workspace/fx/",
      "/v2/workspace/fx/usd",
      "/v2/workspace/mail-sync/",
      "/v2/workspace/mail-sync/contacto",
      "/v2/workspace/mail-quote-numbers/",
      "/v2/workspace/mail-quote-numbers/CN09901",
      "/v2/workspace/mail-quote-numbersx",
      "/v2/cockpit",
      "/v2/cockpit/work-queue/",
      "/v2/cockpit/work-queue/1",
      "/v2/cockpit/kpis",
      "/v2/cockpit/opportunities",
      `/v2/cockpit/opportunities/${uuid}`,
      `/v2/cockpit/opportunities/${uuid}/timeline`,
      "/v2/cockpit/quotations",
      `/v2/cockpit/quotations/${uuid}`,
      `/v2/cockpit/evidence/${uuid}`,
      "/v2/cockpit/search",
      "/v2/cockpit/case-archive",
      "/v2/cockpit/import-review",
      `/v2/cockpit/import-review/documents/${sha}`,
      "/v2/commands/set-case-organization-role",
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("forwards exactly the twelve Marketing reads, GET only, and nothing near them", async () => {
    const { isAllowedMarketingCommandPostPath } = await import("./allowlist");
    const uuid = "96301691-af05-51ea-82e3-05f5fae40837";
    for (const path of [
      "/v2/workspace/marketing",
      "/v2/workspace/marketing/taxonomy",
      "/v2/workspace/marketing/audience",
      `/v2/workspace/marketing/campaigns/${uuid}`,
      `/v2/workspace/marketing/campaigns/${uuid}/freeze-preview`,
      `/v2/workspace/marketing/campaigns/${uuid}/recipients`,
      `/v2/workspace/marketing/campaigns/${uuid}/archive`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/recipients`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/replies`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/audit`,
      "/v2/workspace/marketing/suppressions",
      "/v2/workspace/marketing/campaign-blocks",
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(true);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
    for (const path of [
      "/v2/workspace/marketing/",
      "/v2/workspace/marketing/campaigns",
      "/v2/workspace/marketing/campaigns/not-a-uuid",
      `/v2/workspace/marketing/campaigns/${uuid.toUpperCase()}`,
      `/v2/workspace/marketing/campaigns/${uuid}/recipients/x`,
      `/v2/workspace/marketing/campaigns/${uuid}/send`,
      "/v2/workspace/marketing/audience/export",
      `/v2/workspace/marketing/campaigns/${uuid}/archive/`,
      `/v2/workspace/marketing/campaigns/${uuid}/archive/html`,
      `/v2/workspace/marketing/campaigns/${uuid}/archive.html`,
      `/v2/workspace/marketing/campaigns/${uuid}/history`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/recipients/export`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/replies/sync`,
      `/v2/workspace/marketing/campaigns/${uuid}/history/gmail`,
      `/v2/workspace/marketing/campaigns/${uuid.toUpperCase()}/history/audit`,
      "/v2/workspace/marketing/campaigns/archive",
      "/v2/workspace/marketing/calendar",
      "/v2/workspace/marketing/suppressions/",
      "/v2/workspace/marketing/suppressions/export",
      "/v2/workspace/marketing/campaign-blocks/",
      "/v2/workspace/marketing/campaign-blocks/export",
      `/v2/workspace/marketing/campaign-blocks/${uuid}`,
      "/v2/workspace/marketing/unsubscribe",
      "/v2/unsubscribe/preview",
      "/v2/unsubscribe",
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
    expect(isAllowedMarketingCommandPostPath("/v2/commands/freeze-campaign-audience?x=1")).toBe(true);
  });

  it("forwards exactly the two CRM card reads, GET only, and nothing near them", () => {
    for (const path of ["/v2/workspace/providers", "/v2/workspace/equipment-interests"]) {
      expect(isAllowedUpstreamPath(path), path).toBe(true);
      expect(isAllowedUpstreamPath(`${path}?x=1`), path).toBe(true);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
    for (const path of [
      "/v2/workspace",
      "/v2/workspace/",
      "/v2/workspace/providers/",
      "/v2/workspace/providers/directory",
      "/v2/workspace/provider",
      "/v2/workspace/providersx",
      "/v2/workspace/Providers",
      "/v2/workspace/equipment-interests/",
      "/v2/workspace/equipment-interests/persons",
      "/v2/workspace/equipment-interest",
      "/v2/workspace/equipment_interests",
      "/v2/workspace/equipment-interests.json",
      "/v2/workspace/equipment-interests%2F..%2Fpipeline",
      "/workspace/providers",
      "/v2/providers",
      "/v2/equipment-interests",
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("refuses the bare V1 prefixes and does not confuse them with their V2 namesakes", () => {
    for (const path of ["/contacts", "/contacts/", "/mirror", "/mirror/"]) {
      expect(isAllowedUpstreamPath(path)).toBe(false);
    }
    expect(isAllowedUpstreamPath("/v2/contacts")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/mirror/commercial/deals")).toBe(false);
  });

  it("keeps representative write and non-dashboard paths blocked", () => {
    expect(isAllowedUpstreamPath("/emails")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/send")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/queues/not_a_real_queue")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/send")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/institutions/id/extra")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/id/extra")).toBe(false);
    expect(
      isAllowedUpstreamPath(
        "/operator/procurement/tenders/745712-19-LP26/attachment-navigation/extra",
      ),
    ).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/")).toBe(false);
    // Path-traversal / deeper-segment variants must never sneak past the
    // single-segment tender_code allowlist regex.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/../status")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/id/../../status")).toBe(false);
    expect(isAllowedUpstreamPath("/api/operator/status")).toBe(false);
    // Legacy equipment HTTP surface is retired (apps/api no longer serves this
    // route); the dashboard's actionable-opportunity summary now sources from
    // /operator/procurement/status (W1).
    expect(isAllowedUpstreamPath("/opportunities/equipment")).toBe(false);
  });
});

describe("buildUpstreamUrl", () => {
  it("joins upstream base, path, and query", () => {
    expect(buildUpstreamUrl("https://api.example.com", "/health", "")).toBe(
      "https://api.example.com/health",
    );
    expect(buildUpstreamUrl("https://api.example.com/", "/cases/warm", "?limit=20")).toBe(
      "https://api.example.com/cases/warm?limit=20",
    );
  });
});

describe("operator identity is never derived by the Worker", () => {
  it("drops a browser-sent operator header and ignores a Cloudflare Access header", async () => {
    const { buildUpstreamHeaders, OPERATOR_EMAIL_HEADER } = await import("./proxy");
    const incoming = new Headers({
      "Cf-Access-Authenticated-User-Email": "Tatiana@OrigenLab.CL",
      "X-OriginLab-Operator-Email": "spoofed@attacker.example",
    });
    const headers = buildUpstreamHeaders(
      { ORIGENLAB_API_UPSTREAM: "https://api.example.com", ORIGENLAB_API_AUTH_TOKEN: "secret" },
      incoming,
    );
    expect(headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
    expect(headers.has("Cf-Access-Authenticated-User-Email")).toBe(false);
  });
});


describe("V2 durable read boundary allowlist", () => {
  it("allows exactly the ten V2 listing paths", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of [
      "/v2/contacts",
      "/v2/organizations",
      "/v2/prospects",
      "/v2/opportunities/active",
      "/v2/tasks/due",
      "/v2/review/summary",
      "/v2/quotes/followup",
      "/v2/evidence",
      "/v2/evidence/records",
      "/v2/cases",
    ]) {
      expect(isAllowedUpstreamPath(path)).toBe(true);
    }
  });

  it("allows a card path only when the identifier is UUID-shaped", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    const id = "96301691-af05-51ea-82e3-05f5fae40837";
    expect(isAllowedUpstreamPath(`/v2/contacts/${id}`)).toBe(true);
    expect(isAllowedUpstreamPath(`/v2/organizations/${id}`)).toBe(true);
    expect(isAllowedUpstreamPath(`/v2/cases/${id}`)).toBe(true);
    // Uppercase, a wrong length and a trailing sub-resource are all refused: the shape is
    // the allowlist, not a `.+` that would forward whatever the browser asked for.
    expect(isAllowedUpstreamPath(`/v2/contacts/${id.toUpperCase()}`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/contacts/${id}/evidence`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/prospects/${id}`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/evidence/${id}`)).toBe(false);
    // A case has sub-resources upstream in every direction a command could take. None of
    // them is a path this Worker knows, and the card path does not widen into them.
    expect(isAllowedUpstreamPath(`/v2/cases/${id}/organizations`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/cases/${id}/stage`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/cases/${id.toUpperCase()}`)).toBe(false);
    // The one named sub-resource under an organization, and only that one.
    expect(isAllowedUpstreamPath(`/v2/organizations/${id}/cases`)).toBe(true);
    expect(isAllowedUpstreamPath(`/v2/organizations/${id}/cases?limit=50`)).toBe(true);
    expect(isAllowedUpstreamPath(`/v2/organizations/${id}/quotes`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/organizations/${id}/cases/${id}`)).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/organizations/${id.toUpperCase()}/cases`)).toBe(false);
  });

  it("allows the listed paths with a query string", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    expect(isAllowedUpstreamPath("/v2/contacts?limit=50&offset=0")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/tasks/due?horizon_days=0")).toBe(true);
  });

  it("refuses any V2 path that is not listed by name", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    // The list is exact on purpose. A future V2 command route must not become
    // reachable through this Worker just because it lives under /v2.
    for (const path of [
      "/v2",
      "/v2/",
      "/v2/contacts/123",
      "/v2/organizations/abc",
      "/v2/commands/promote",
      "/v2/contacts/96301691-af05-51ea-82e3-05f5fae40837/merge",
      "/v2/review",
      "/v2/review/summary/extra",
      "/v2/tasks",
      "/v2/quotes",
      "/v2/opportunities",
      // The record queue is one literal path. Nothing else under it is reachable, so a
      // per-record sub-resource -- the shape a promote command would take -- is refused.
      "/v2/evidence/records/96301691-af05-51ea-82e3-05f5fae40837",
      "/v2/evidence/records/promote",
      "/v2/evidence/record",
      "/v2/case",
      "/v2/cases/",
      "/v2/cases/123",
      "/v2/cases/open",
      "/v2/../operations/work-queue",
    ]) {
      expect(isAllowedUpstreamPath(path)).toBe(false);
    }
  });

  it("does not make any V2 path POST-writable", async () => {
    const { isAllowedPostPath } = await import("./allowlist");
    for (const path of [
      "/v2/contacts",
      "/v2/organizations",
      "/v2/review/summary",
      "/v2/evidence",
      "/v2/evidence/records",
      "/v2/contacts/96301691-af05-51ea-82e3-05f5fae40837",
      "/v2/organizations/96301691-af05-51ea-82e3-05f5fae40837",
      "/v2/cases",
      "/v2/cases/96301691-af05-51ea-82e3-05f5fae40837",
      "/v2/commands/promote",
    ]) {
      expect(isAllowedPostPath(path)).toBe(false);
    }
  });

  // The eight non-sending Marketing commands. The one command that sends — the admin-only test
  // send of one campaign email to one address — has its own test below.
  it("allows the eight non-sending Marketing commands as POST, and no campaign send, approve, schedule or activate", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of [
      "/v2/commands/create-campaign-draft",
      "/v2/commands/save-campaign-draft",
      "/v2/commands/freeze-campaign-audience",
      "/v2/commands/set-campaign-planning",
      "/v2/commands/resolve-unsubscribe-review",
      "/v2/commands/dismiss-unsubscribe-review",
      "/v2/commands/block-campaign",
      "/v2/commands/unblock-campaign",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(true);
      expect(isAllowedUpstreamPath(path), path).toBe(false); // never GET-readable
    }
    for (const path of [
      "/v2/commands/send-campaign",
      "/v2/commands/approve-campaign",
      "/v2/commands/activate-campaign",
      "/v2/commands/dry-run-campaign",
      "/v2/commands/grant-recontact-override",
      "/v2/commands/freeze-campaign-audience/",
      "/v2/commands/freeze-campaign-audience-and-send",
      "/v2/commands/set-campaign-planning/",
      "/v2/commands/set-campaign-planning-and-send",
      "/v2/commands/schedule-campaign",
      "/v2/commands/schedule-campaign-send",
      "/v2/commands/enqueue-campaign",
      "/v2/commands/clear-campaign-planning",
      "/v2/commands/SET-CAMPAIGN-PLANNING",
      // Campaign blocks: exactly the two commands; nothing that resumes, expires or sends.
      "/v2/commands/block-campaign/",
      "/v2/commands/unblock-campaign-and-send",
      "/v2/commands/resume-campaign",
      "/v2/commands/pause-campaign",
      "/v2/commands/expire-campaign-block",
      "/v2/commands/BLOCK-CAMPAIGN",
      // W10: the unsubscribe preview and apply are API-only operator tooling, never the browser's.
      "/v2/commands/apply-unsubscribe-replies",
      "/v2/unsubscribe/preview",
      "/v2/commands/resolve-unsubscribe-review/",
      "/v2/commands/dismiss-unsubscribe-review-all",
      "/v2/commands/lift-unsubscribe",
      "/v2/commands/resubscribe",
      "/v2/commands/revoke-block",
      "/v2/commands/sync-gmail-replies",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });

  it("allows the test send as an exact POST with a small body, and its history as a GET", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath, marketingCommandMaxBytes } = await import("./allowlist");
    expect(isAllowedPostPath("/v2/commands/send-campaign-test")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/commands/send-campaign-test")).toBe(false);
    expect(marketingCommandMaxBytes("/v2/commands/send-campaign-test")).toBe(4_096);
    expect(isAllowedUpstreamPath("/v2/workspace/marketing/test-send-history?v1_lane_key=cyber-2026-10")).toBe(true);
    for (const p of ["/v2/commands/send-campaign", "/v2/commands/send-campaign-test/", "/v2/commands/send-campaign-tests",
                     "/v2/workspace/marketing/test-send-history/x"]) {
      expect(isAllowedPostPath(p), p).toBe(false);
      expect(isAllowedUpstreamPath(p), p).toBe(false);
    }
  });

  it("lets exactly four case commands through and keeps the other ten evidence-bound routes unreachable", async () => {
    // The command boundary EXISTS in apps/api: POST /v2/commands/* records durable human
    // decisions -- six about staged evidence (including confirm-person-from-evidence), and nine
    // about a commercial case. Building that boundary and letting a browser reach it are two
    // separate decisions. The second has been taken for exactly four case commands, the ones the
    // case drawer uses: advance-case-stage («Cambiar etapa»), record-case-won («Marcar
    // ganada»), resolve-current-revision («Elegir revisión vigente») and record-case-quotation
    // («Registrar cotización», «Nueva revisión»). Every other one stays refused, as POST and as GET.
    //
    // The list is written out in full on purpose. A route added to apps/api and forgotten
    // here would be forgotten silently; a route added here that does not exist costs one
    // redundant assertion, which is the cheaper mistake.
    const { CASE_COMMAND_POST_PATHS, isAllowedCaseCommandPostPath, isAllowedPostPath, isAllowedUpstreamPath } =
      await import("./allowlist");
    for (const path of [
      // evidence review
      "/v2/commands/keep-evidence-pending",
      "/v2/commands/confirm-organization",
      "/v2/commands/create-organization",
      "/v2/commands/attach-contact-address",
      "/v2/commands/attribute-sender-organization",
      "/v2/commands/confirm-person-from-evidence",
      // the commercial case, minus the four the drawer uses
      "/v2/commands/open-commercial-case",
      "/v2/commands/link-case-evidence",
      "/v2/commands/add-case-organization",
      "/v2/commands/set-case-organization-role",
      "/v2/commands/record-case-interest",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
    const allowed = [
      "/v2/commands/advance-case-stage",
      "/v2/commands/record-case-won",
      "/v2/commands/resolve-current-revision",
      "/v2/commands/record-case-quotation",
      // W11 tasks: «En pausa hasta…», «Hecho», «Retomar ahora».
      "/v2/commands/create-task",
      "/v2/commands/complete-task",
      "/v2/commands/cancel-task",
    ];
    expect(CASE_COMMAND_POST_PATHS).toHaveLength(7);
    for (const path of allowed) {
      expect(isAllowedCaseCommandPostPath(path), path).toBe(true);
      expect(isAllowedPostPath(path), path).toBe(true);
      // A command is never a read.
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });

  it("matches the four case command paths exactly, never a neighbour", async () => {
    const { isAllowedPostPath, marketingCommandMaxBytes } = await import("./allowlist");
    expect(marketingCommandMaxBytes("/v2/commands/advance-case-stage")).toBe(16_384);
    expect(marketingCommandMaxBytes("/v2/commands/record-case-won")).toBe(16_384);
    expect(marketingCommandMaxBytes("/v2/commands/resolve-current-revision")).toBe(16_384);
    expect(marketingCommandMaxBytes("/v2/commands/record-case-quotation")).toBe(16_384);
    expect(marketingCommandMaxBytes("/v2/commands/create-task")).toBe(16_384);
    for (const path of [
      "/v2/commands/advance-case-stage/",
      "/v2/commands/advance-case-stage-all",
      "/v2/commands/record-case-won/x",
      "/v2/commands/record-case-lost",
      "/v2/commands/record-case-wonder",
      "/v2/commands/RECORD-CASE-WON",
      "/v2/commands/correct-case-stage",
      "/v2/commands/unlink-case-evidence",
      "/v2/commands/resolve-current-revision/",
      "/v2/commands/resolve-current-revisions",
      "/v2/commands/record-case-quotation/x",
      "/v2/commands/record-historical-quotation",
      "/v2/commands/void-historical-quote-revision",
      "/v2/commands/RECORD-CASE-QUOTATION",
      "/v2/commands/create-task/",
      "/v2/commands/create-tasks",
      "/v2/commands/delete-task",
      "/v2/commands/update-task",
      "/v2/commands/cancel-task/x",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });
});

describe("CRM authoring command POST allowlist", () => {
  const ALL_AUTHORING_PATHS = [
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

  it("isAllowedCrmAuthoringCommandPostPath allows all 29 exact paths", async () => {
    const { isAllowedCrmAuthoringCommandPostPath } = await import("./allowlist");
    for (const path of ALL_AUTHORING_PATHS) {
      expect(isAllowedCrmAuthoringCommandPostPath(path), path).toBe(true);
    }
    // Query string is stripped: path still matches
    expect(isAllowedCrmAuthoringCommandPostPath("/v2/commands/create-person?x=1")).toBe(true);
  });

  it("isAllowedPostPath admits all 29 authoring paths", async () => {
    const { isAllowedPostPath } = await import("./allowlist");
    for (const path of ALL_AUTHORING_PATHS) {
      expect(isAllowedPostPath(path), path).toBe(true);
    }
  });

  it("refuses neighbours: trailing slash, uppercase, -and-delete, delete-person, bare /v2/commands/", async () => {
    const { isAllowedCrmAuthoringCommandPostPath, isAllowedPostPath } = await import("./allowlist");
    for (const path of [
      "/v2/commands/create-person/",           // trailing slash
      "/v2/commands/Create-Person",            // uppercase
      "/v2/commands/create-person-and-delete", // -and-delete neighbour
      "/v2/commands/delete-person",            // delete variant
      "/v2/commands/",                         // bare prefix
      "/v2/commands/archive-person/extra",     // extra segment
      "/v2/commands/ADD-NOTE",                 // uppercase
      "/v2/commands/archive-note/",            // trailing slash
      "/v2/commands/register-organization-and-send",
      "/v2/commands/confirm-supplier-candidate/approve",
    ]) {
      expect(isAllowedCrmAuthoringCommandPostPath(path), path).toBe(false);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("none of the 29 authoring command paths are GET-readable", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of ALL_AUTHORING_PATHS) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });
});

describe("CRM part A: confirm an institution, suggested people", () => {
  it("confirm-organization-record is an exact authoring POST; the evidence-bound confirm-organization stays refused", async () => {
    const { isAllowedCrmAuthoringCommandPostPath, isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    expect(isAllowedCrmAuthoringCommandPostPath("/v2/commands/confirm-organization-record")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/commands/confirm-organization-record")).toBe(false);
    for (const path of [
      "/v2/commands/confirm-organization",
      "/v2/commands/confirm-organization-record/",
      "/v2/commands/confirm-organization-records",
      "/v2/commands/Confirm-Organization-Record",
      "/v2/commands/confirm-organization-record/extra",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("forwards GET of one case's notes by its exact path, never as a POST", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    const uuid = "96301691-af05-41ea-82e3-05f5fae40837";
    expect(isAllowedUpstreamPath(`/v2/workspace/opportunities/${uuid}/notes`)).toBe(true);
    expect(isAllowedPostPath(`/v2/workspace/opportunities/${uuid}/notes`)).toBe(false);
    for (const p of [
      `/v2/workspace/opportunities/${uuid}`,
      `/v2/workspace/opportunities/${uuid}/notes/`,
      `/v2/workspace/opportunities/${uuid}/notes/x`,
      `/v2/workspace/opportunities/${uuid}/events`,
      `/v2/workspace/opportunities/${uuid.toUpperCase()}/notes`,
      "/v2/workspace/opportunities/not-a-uuid/notes",
      "/v2/workspace/opportunities",
    ]) {
      expect(isAllowedUpstreamPath(p), p).toBe(false);
    }
  });

  it("forwards GET of one case's mail documents by its exact path, never as a POST", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    const uuid = "96301691-af05-41ea-82e3-05f5fae40837";
    expect(isAllowedUpstreamPath(`/v2/workspace/opportunities/${uuid}/mail-documents`)).toBe(true);
    expect(isAllowedPostPath(`/v2/workspace/opportunities/${uuid}/mail-documents`)).toBe(false);
    for (const p of [
      `/v2/workspace/opportunities/${uuid}/mail-documents/`,
      `/v2/workspace/opportunities/${uuid}/mail-documents/x`,
      `/v2/workspace/opportunities/${uuid}/mail`,
      `/v2/workspace/opportunities/${uuid.toUpperCase()}/mail-documents`,
      "/v2/workspace/opportunities/not-a-uuid/mail-documents",
    ]) {
      expect(isAllowedUpstreamPath(p), p).toBe(false);
    }
  });

  it("forwards GET /v2/workspace/person-suggestions by its exact path, never as a POST", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    expect(isAllowedUpstreamPath("/v2/workspace/person-suggestions")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/workspace/person-suggestions?x=1")).toBe(true);
    expect(isAllowedPostPath("/v2/workspace/person-suggestions")).toBe(false);
    for (const path of [
      "/v2/workspace/person-suggestions/",
      "/v2/workspace/person-suggestions/extra",
      "/v2/workspace/person-suggestion",
      "/v2/workspace/Person-Suggestions",
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });
});

describe("CRM authoring GET reads allowlist", () => {
  const uuid = "96301691-af05-51ea-82e3-05f5fae40837";

  it("forwards the three exact authoring reads, GET only", async () => {
    const { isAllowedUpstreamPath, isAllowedPostPath } = await import("./allowlist");
    const allowed = [
      `/v2/workspace/people/${uuid}`,
      "/v2/workspace/people/merge-preview",
      `/v2/workspace/organizations/${uuid}/authoring`,
    ];
    for (const path of allowed) {
      expect(isAllowedUpstreamPath(path), path).toBe(true);
      expect(isAllowedUpstreamPath(`${path}?x=1`), path).toBe(true);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("refuses the bare people list and per-person notes (not yet a browser surface)", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    expect(isAllowedUpstreamPath("/v2/workspace/people")).toBe(false);
    expect(isAllowedUpstreamPath("/v2/workspace/people/")).toBe(false);
    expect(isAllowedUpstreamPath(`/v2/workspace/people/${uuid}/notes`)).toBe(false);
  });

  it("refuses neighbours: uppercase uuid, trailing slash, extra segment, wrong shape", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of [
      `/v2/workspace/people/${uuid.toUpperCase()}`,
      `/v2/workspace/people/${uuid}/`,
      `/v2/workspace/people/${uuid}/extra`,
      "/v2/workspace/people/not-a-uuid",
      "/v2/workspace/people/merge-preview/",
      "/v2/workspace/people/merge-preview/extra",
      `/v2/workspace/organizations/${uuid}/authoring/`,
      `/v2/workspace/organizations/${uuid}/authoring/extra`,
      `/v2/workspace/organizations/${uuid.toUpperCase()}/authoring`,
      `/v2/workspace/organizations/${uuid}`,
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });
});

describe("V1 surfaces are refused on the browser boundary", () => {
  const V1_GET_PATHS = [
    "/operator/status",
    "/operator/automation-status",
    "/operator/procurement/status",
    "/operator/procurement/institutions",
    "/operator/procurement/institutions/test-institution-id",
    "/operator/procurement/queues/current_opportunity",
    "/operator/procurement/tenders/745712-14-LE26",
    "/operator/procurement/tenders/745712-14-LE26/attachment-navigation",
    "/cases/warm",
    "/opportunities/commercial",
    "/opportunities/commercial/o_" + "a".repeat(32),
    "/operations/work-queue",
    "/operations/sales-opportunities",
    "/operations/sales-opportunities/sales_" + "a".repeat(32),
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/activities",
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/tasks",
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/quotes",
    "/operations/customer-quotes",
    "/operations/customer-quotes/quote_" + "a".repeat(32),
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/events",
    "/operations/customer-quotes/drive-pending",
    "/operations/customer-quotes/drive-pending/resolve",
    "/operations/opportunities/o_" + "a".repeat(32) + "/state",
    "/operations/opportunities/o_" + "a".repeat(32) + "/activities",
    "/operations/opportunities/o_" + "a".repeat(32) + "/tasks",
  ];
  const V1_POST_PATHS = [
    "/operator/procurement/tenders/745712-14-LE26/annex-bundle/preview",
    "/operator/procurement/tenders/745712-14-LE26/annex-bundle/import",
    "/operations/opportunities/o_" + "a".repeat(32) + "/state",
    "/operations/sales-opportunities/promote",
    "/operations/sales-opportunities/manual",
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/stage",
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/quotes",
    "/operations/sales-opportunities/sales_" + "a".repeat(32) + "/quotes/adopt-drive-folder",
    "/operations/activities",
    "/operations/tasks",
    "/operations/tasks/task_" + "a".repeat(32) + "/complete",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/drive-workspace",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/submit-for-review",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/approve",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/confirm-send",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/close",
    "/operations/tasks/task_" + "a".repeat(32) + "/cancel",
    "/operations/customer-quotes/quote_" + "a".repeat(32) + "/request-adjustments",
  ];

  it.each(V1_GET_PATHS)("refuses GET %s", (path) => {
    expect(isAllowedUpstreamPath(path)).toBe(false);
  });

  it.each(V1_POST_PATHS)("refuses POST %s", (path) => {
    expect(isAllowedPostPath(path)).toBe(false);
  });

  it("still allows exactly /health, the auth routes and the named v2 reads", () => {
    for (const path of ["/health", "/auth/session", "/auth/profiles", "/auth/google/login", "/auth/google/callback", "/v2/workspace/overview", "/v2/workspace/pipeline", "/v2/cases"]) {
      expect(isAllowedUpstreamPath(path)).toBe(true);
    }
  });
});

describe("mail triage review", () => {
  it("allows the queue as an exact GET and the verdict as an exact POST, and nothing near them", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath, marketingCommandMaxBytes } = await import("./allowlist");
    expect(isAllowedUpstreamPath("/v2/workspace/triage-readings")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/workspace/triage-readings?status=reviewed&limit=50")).toBe(true);
    expect(isAllowedPostPath("/v2/workspace/triage-readings")).toBe(false);
    expect(isAllowedPostPath("/v2/commands/review-triage")).toBe(true);
    expect(isAllowedUpstreamPath("/v2/commands/review-triage")).toBe(false);
    expect(marketingCommandMaxBytes("/v2/commands/review-triage")).toBe(131_072);
    for (const path of ["/v2/workspace/triage-readings/", "/v2/workspace/triage-readings/x", "/v2/workspace/triage",
                        "/v2/commands/review-triage/", "/v2/commands/review-triage-all", "/v2/commands/REVIEW-TRIAGE"]) {
      expect(isAllowedPostPath(path), path).toBe(false);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });
});

describe("email → cases rules (admin only upstream)", () => {
  it("allows the dry run as an exact GET and the two commands as exact POSTs", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath, marketingCommandMaxBytes } = await import("./allowlist");
    expect(isAllowedUpstreamPath("/v2/workspace/mail-rules/preview")).toBe(true);
    expect(isAllowedPostPath("/v2/workspace/mail-rules/preview")).toBe(false);
    for (const path of ["/v2/commands/apply-mail-rules", "/v2/commands/undo-mail-rule-action", "/v2/commands/set-auto-mail-rules"]) {
      expect(isAllowedPostPath(path), path).toBe(true);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
      expect(marketingCommandMaxBytes(path)).toBe(131_072);
    }
  });

  it("refuses every neighbour of the three paths", async () => {
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of [
      "/v2/workspace/mail-rules",
      "/v2/workspace/mail-rules/",
      "/v2/workspace/mail-rules/preview/",
      "/v2/workspace/mail-rules/apply",
      "/v2/commands/apply-mail-rules/",
      "/v2/commands/apply-mail-rules-all",
      "/v2/commands/undo-mail-rule-action/x",
      "/v2/commands/set-auto-mail-rules/",
      "/v2/commands/set-auto-mail-rules-all",
      "/v2/commands/auto-mail-rules",
      "/v2/commands/APPLY-MAIL-RULES",
      // The case commands the rules call stay unreachable from the browser, except the four the
      // case drawer uses (CASE_COMMAND_POST_PATHS), which have their own list.
      "/v2/commands/open-commercial-case",
      "/v2/commands/link-case-evidence",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });
});

describe("Catalog 1a", () => {
  const uuid = "96301691-af05-51ea-82e3-05f5fae40837";
  const GET_PATHS = [
    "/v2/catalog/products",
    `/v2/catalog/products/${uuid}`,
    `/v2/catalog/suppliers/${uuid}/terms`,
    "/v2/catalog/parameters",
    "/v2/catalog/fx",
    "/v2/catalog/price-history",
    `/v2/catalog/images/${uuid}/url`,
  ];
  const JSON_COMMANDS = [
    "create-product",
    "update-product",
    "confirm-product-content",
    "record-supplier-cost",
    "set-supplier-terms",
    "set-cost-parameter",
    "record-fx-rate",
    "update-product-image",
    "review-document-line",
  ].map((name) => `/v2/commands/${name}`);
  const UPLOAD = "/v2/commands/add-product-image";

  it("allows each catalog GET, with a query string, and never as a POST", () => {
    for (const path of GET_PATHS) {
      expect(isAllowedUpstreamPath(path), path).toBe(true);
      expect(isAllowedUpstreamPath(`${path}?model_key=x&limit=5`), path).toBe(true);
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });

  it("refuses catalog GET neighbours: uppercase uuid, trailing slash, extra segment, wrong shape", () => {
    for (const path of [
      `/v2/catalog/products/${uuid.toUpperCase()}`,
      `/v2/catalog/products/${uuid}/`,
      `/v2/catalog/products/${uuid}/extra`,
      "/v2/catalog/products/",
      "/v2/catalog/products/not-a-uuid",
      "/v2/catalog",
      "/v2/catalog/",
      "/v2/catalog/fx/",
      "/v2/catalog/fx/extra",
      "/v2/catalog/parameters/extra",
      "/v2/catalog/price-history/extra",
      `/v2/catalog/suppliers/${uuid}`,
      `/v2/catalog/suppliers/${uuid}/terms/`,
      `/v2/catalog/suppliers/${uuid.toUpperCase()}/terms`,
      `/v2/catalog/images/${uuid}`,
      `/v2/catalog/images/${uuid}/url/`,
      `/v2/catalog/images/${uuid.toUpperCase()}/url`,
      `/v2/catalog/images/${uuid}/bytes`,
    ]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });

  it("allows the nine JSON commands and the upload as exact POST paths, never as GET", () => {
    for (const path of [...JSON_COMMANDS, UPLOAD]) {
      expect(isAllowedPostPath(path), path).toBe(true);
      expect(isAllowedPostPath(`${path}?x=1`), path).toBe(true);
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });

  it("refuses command neighbours", () => {
    for (const path of [
      "/v2/commands/create-product/",
      "/v2/commands/Create-Product",
      "/v2/commands/create-product/extra",
      "/v2/commands/create-products",
      "/v2/commands/add-product-image/",
      "/v2/commands/add-product-image/extra",
      "/v2/commands/Add-Product-Image",
      "/v2/commands/delete-product",
      "/v2/commands/delete-product-image",
      "/v2/catalog/products",
    ]) {
      expect(isAllowedPostPath(path), path).toBe(false);
    }
  });
});
