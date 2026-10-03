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

  it("allows exactly the seven CRM workspace reads, GET only", () => {
    for (const path of [
      "/v2/workspace/overview",
      "/v2/workspace/pipeline",
      "/v2/workspace/drive",
      "/v2/workspace/review",
      "/v2/cockpit/work-queue",
    ]) {
      expect(isAllowedUpstreamPath(path)).toBe(true);
      expect(isAllowedPostPath(path)).toBe(false);
    }
    expect(isAllowedUpstreamPath("/v2/cockpit/work-queue?limit=200")).toBe(true);
  });

  it("keeps every other workspace and cockpit path refused", () => {
    // Built upstream, GET-only and redacting, but not a browser surface: each of these is
    // a separate decision. Neighbours of the seven allowed paths are refused too.
    const uuid = "96301691-af05-51ea-82e3-05f5fae40837";
    const sha = "a".repeat(64);
    for (const path of [
      "/v2/workspace",
      "/v2/workspace/",
      "/v2/workspace/overview/",
      "/v2/workspace/pipeline/extra",
      `/v2/workspace/pipeline/${uuid}`,
      "/v2/workspace/other",
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

  it("allows exactly the eight Marketing commands as POST, and no send, approve, schedule or activate", async () => {
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

  it("keeps the twelve evidence-bound V2 command routes unreachable through this Worker", async () => {
    // The command boundary EXISTS in apps/api: POST /v2/commands/* records durable human
    // decisions -- six about staged evidence (including confirm-person-from-evidence), and six
    // about a commercial case, which include opening one, naming who is asking, and moving it
    // through its stages. Building that boundary and letting a browser reach it are two separate
    // decisions, and only the first has been taken. Until the second is taken deliberately, the
    // Worker forwards neither the method nor the path -- so the operator workspace stays a
    // preview by construction rather than by discipline.
    //
    // The list is written out in full on purpose. A route added to apps/api and forgotten
    // here would be forgotten silently; a route added here that does not exist costs one
    // redundant assertion, which is the cheaper mistake.
    const { isAllowedPostPath, isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of [
      // evidence review
      "/v2/commands/keep-evidence-pending",
      "/v2/commands/confirm-organization",
      "/v2/commands/create-organization",
      "/v2/commands/attach-contact-address",
      "/v2/commands/attribute-sender-organization",
      "/v2/commands/confirm-person-from-evidence",
      // the commercial case
      "/v2/commands/open-commercial-case",
      "/v2/commands/link-case-evidence",
      "/v2/commands/add-case-organization",
      "/v2/commands/set-case-organization-role",
      "/v2/commands/record-case-interest",
      "/v2/commands/advance-case-stage",
    ]) {
      expect(isAllowedPostPath(path)).toBe(false);
      expect(isAllowedUpstreamPath(path)).toBe(false);
    }
  });
});

describe("CRM authoring command POST allowlist", () => {
  const ALL_28_AUTHORING_PATHS = [
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

  it("isAllowedCrmAuthoringCommandPostPath allows all 28 exact paths", async () => {
    const { isAllowedCrmAuthoringCommandPostPath } = await import("./allowlist");
    for (const path of ALL_28_AUTHORING_PATHS) {
      expect(isAllowedCrmAuthoringCommandPostPath(path), path).toBe(true);
    }
    // Query string is stripped: path still matches
    expect(isAllowedCrmAuthoringCommandPostPath("/v2/commands/create-person?x=1")).toBe(true);
  });

  it("isAllowedPostPath admits all 28 authoring paths", async () => {
    const { isAllowedPostPath } = await import("./allowlist");
    for (const path of ALL_28_AUTHORING_PATHS) {
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

  it("none of the 28 authoring command paths are GET-readable", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    for (const path of ALL_28_AUTHORING_PATHS) {
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
