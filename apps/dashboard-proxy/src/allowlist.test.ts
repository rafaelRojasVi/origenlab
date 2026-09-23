import { describe, expect, it } from "vitest";

import { isAllowedPostUploadPath, isAllowedUpstreamPath, stripApiPrefix } from "../src/allowlist";
import { buildUpstreamUrl } from "../src/proxy";

describe("allowlist", () => {
  const PRODUCTION_SMOKE_PATHS = [
    "/health",
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
    "/mirror/catalog/products",
    "/mirror/leads/summary",
    "/mirror/leads/prospects",
    "/mirror/audits/gmail-interactions",
    "/mirror/commercial/deals",
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

  it("isAllowedUpstreamPath allows dashboard read routes only", () => {
    expect(isAllowedUpstreamPath("/health")).toBe(true);
    expect(isAllowedUpstreamPath("/operator/status")).toBe(true);
    expect(isAllowedUpstreamPath("/cases/warm")).toBe(true);
    expect(isAllowedUpstreamPath("/contacts/a@b.co")).toBe(true);
    expect(isAllowedUpstreamPath("/mirror/commercial/deals")).toBe(true);
    expect(isAllowedUpstreamPath("/emails")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/send")).toBe(false);
  });

  it.each(PRODUCTION_SMOKE_PATHS)("allows production smoke path %s", (path) => {
    expect(isAllowedUpstreamPath(path)).toBe(true);
    expect(isAllowedUpstreamPath(`${path}?limit=20`)).toBe(true);
  });

  it("allows the PR3 machine-opportunity intake list and detail reads only", () => {
    const opportunityId = "o_0123456789abcdef0123456789abcdef";

    expect(isAllowedUpstreamPath("/opportunities/commercial")).toBe(true);
    expect(isAllowedUpstreamPath(`/opportunities/commercial/${opportunityId}`)).toBe(true);
    expect(
      isAllowedUpstreamPath("/opportunities/commercial?limit=20&canonical_stage=quote_sent"),
    ).toBe(true);

    expect(isAllowedUpstreamPath("/opportunities/commercial/")).toBe(false);
    expect(isAllowedUpstreamPath("/opportunities/commercial/not-an-id")).toBe(false);
    expect(
      isAllowedUpstreamPath(`/opportunities/commercial/${opportunityId}/extra`),
    ).toBe(false);
    expect(isAllowedUpstreamPath("/opportunities/commercial/o_short")).toBe(false);
  });

  it("allows CRM sales-opportunity nested read routes only", () => {
    const salesId = "sales_0123456789abcdef0123456789abcdef";

    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/activities`,
      ),
    ).toBe(true);
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/tasks`,
      ),
    ).toBe(true);

    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/activities/extra`,
      ),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/tasks/extra`,
      ),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath(
        "/operations/sales-opportunities/sales_not-valid/activities",
      ),
    ).toBe(false);
  });

  it("allows the durable sales-opportunity board list route only", () => {
    expect(isAllowedUpstreamPath("/operations/sales-opportunities")).toBe(true);
    expect(
      isAllowedUpstreamPath(
        "/operations/sales-opportunities?stage=new&stage=qualifying&limit=200",
      ),
    ).toBe(true);
    expect(isAllowedUpstreamPath("/operations/sales-opportunities/")).toBe(false);
    expect(isAllowedUpstreamPath("/operations/sales-opportunities/extra/path")).toBe(false);
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

  it("allows real tender_code formats (mixed and lowercase), case preserved", () => {
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26")).toBe(true);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/4291-46-LE26")).toBe(true);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-lp26")).toBe(true);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/4291-46-le26")).toBe(true);
    expect(
      isAllowedUpstreamPath(
        "/operator/procurement/tenders/745712-19-LP26/attachment-navigation",
      ),
    ).toBe(true);
    expect(
      isAllowedUpstreamPath(
        "/operator/procurement/tenders/4291-46-le26/attachment-navigation",
      ),
    ).toBe(true);
  });

  it("rejects tender_code segments that are not a conservative alphanumeric+hyphen token", () => {
    // Dot-segments (path traversal) as the tender_code segment itself.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/..")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/.")).toBe(false);
    // Whitespace in the tender_code segment.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712 19 LP26")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26 ")).toBe(false);
    // Unexpected punctuation in the tender_code segment.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712;19;LP26")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/<script>")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712%2F19")).toBe(false);
    expect(
      isAllowedUpstreamPath(
        "/operator/procurement/tenders/745712%2F19/attachment-navigation",
      ),
    ).toBe(false);
    // Deeper/extra path segments beyond the single tender_code segment.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26/extra")).toBe(false);
  });
});

describe("isAllowedPostUploadPath", () => {
  it("accepts only the exact annex-bundle preview/import paths for a well-formed tender code", () => {
    expect(
      isAllowedPostUploadPath(
        "/operator/procurement/tenders/745712-19-LP26/annex-bundle/preview",
      ),
    ).toBe(true);

    expect(
      isAllowedPostUploadPath(
        "/operator/procurement/tenders/745712-19-LP26/annex-bundle/import",
      ),
    ).toBe(true);
  });

  it("is independent of isAllowedUpstreamPath: the plain tender path is never POST-legal", () => {
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26")).toBe(true);
    expect(isAllowedPostUploadPath("/operator/procurement/tenders/745712-19-LP26")).toBe(false);
  });

  it("rejects every other allowlisted GET path", () => {
    expect(isAllowedPostUploadPath("/operator/procurement/status")).toBe(false);
    expect(isAllowedPostUploadPath("/operator/procurement/institutions")).toBe(false);
    expect(isAllowedPostUploadPath("/operator/procurement/queues/current_opportunity")).toBe(false);
    expect(
      isAllowedPostUploadPath(
        "/operator/procurement/tenders/745712-19-LP26/attachment-navigation",
      ),
    ).toBe(false);
    expect(isAllowedPostUploadPath("/health")).toBe(false);
  });

  it("rejects deeper/extra path segments beyond preview/import", () => {
    expect(
      isAllowedPostUploadPath(
        "/operator/procurement/tenders/745712-19-LP26/annex-bundle/preview/extra",
      ),
    ).toBe(false);

    expect(
      isAllowedPostUploadPath(
        "/operator/procurement/tenders/745712-19-LP26/annex-bundle/import/extra",
      ),
    ).toBe(false);
  });

  it("rejects a malformed tender-code segment", () => {
    expect(isAllowedPostUploadPath("/operator/procurement/tenders/745712 19/annex-bundle/preview")).toBe(false);
    expect(isAllowedPostUploadPath("/operator/procurement/tenders/../annex-bundle/preview")).toBe(false);
  });

  it("strips query string before matching", () => {
    expect(
      isAllowedPostUploadPath("/operator/procurement/tenders/745712-19-LP26/annex-bundle/preview?declare_complete=true"),
    ).toBe(true);
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

describe("commercial operator identity forwarding", () => {
  it("reconstructs operator identity from Cloudflare Access", async () => {
    const { buildUpstreamHeaders, OPERATOR_EMAIL_HEADER } = await import("./proxy");

    const incoming = new Headers({
      "Cf-Access-Authenticated-User-Email": "Tatiana@OrigenLab.CL",
      "X-OriginLab-Operator-Email": "spoofed@attacker.example",
    });

    const headers = buildUpstreamHeaders(
      {
        ORIGENLAB_API_UPSTREAM: "https://api.example.com",
        ORIGENLAB_API_AUTH_TOKEN: "secret",
      },
      incoming,
    );

    expect(headers.get(OPERATOR_EMAIL_HEADER)).toBe(
      "tatiana@origenlab.cl",
    );
    expect(headers.get(OPERATOR_EMAIL_HEADER)).not.toBe(
      "spoofed@attacker.example",
    );
  });

  it("does not invent operator identity when Access identity is absent", async () => {
    const { buildUpstreamHeaders, OPERATOR_EMAIL_HEADER } = await import("./proxy");

    const incoming = new Headers({
      "X-OriginLab-Operator-Email": "spoofed@attacker.example",
    });

    const headers = buildUpstreamHeaders(
      {
        ORIGENLAB_API_UPSTREAM: "https://api.example.com",
        ORIGENLAB_API_AUTH_TOKEN: "secret",
      },
      incoming,
    );

    expect(headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });
});

describe("commercial operations POST allowlist", () => {
  it("admits exactly the intended commercial command shapes", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import("./allowlist");

    const opportunityId = `o_${"a".repeat(32)}`;
    const taskId = `task_${"b".repeat(32)}`;

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/opportunities/${opportunityId}/state`,
      ),
    ).toBe(true);

    expect(
      isAllowedCommercialOperationsPostPath("/operations/activities"),
    ).toBe(true);

    expect(
      isAllowedCommercialOperationsPostPath("/operations/tasks"),
    ).toBe(true);

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/tasks/${taskId}/complete`,
      ),
    ).toBe(true);

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/tasks/${taskId}/cancel`,
      ),
    ).toBe(true);
  });

  it("rejects malformed or broadened commercial command paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import("./allowlist");

    const opportunityId = `o_${"a".repeat(32)}`;
    const taskId = `task_${"b".repeat(32)}`;

    const rejected = [
      "/operations",
      "/operations/",
      "/operations/opportunities",
      "/operations/opportunities/opp_1/state",
      `/operations/opportunities/${opportunityId}`,
      `/operations/opportunities/${opportunityId}/delete`,
      "/operations/activities/extra",
      "/operations/tasks/extra",
      `/operations/tasks/${taskId}`,
      `/operations/tasks/${taskId}/delete`,
      `/operations/tasks/${taskId}/reopen`,
      `/operations/tasks/task_${"b".repeat(31)}/complete`,
      `/operations/tasks/task_${"b".repeat(33)}/complete`,
      `/operations/tasks/task_${"G".repeat(32)}/complete`,
    ];

    for (const path of rejected) {
      expect(
        isAllowedCommercialOperationsPostPath(path),
        path,
      ).toBe(false);
    }
  });

  it("combined POST gate preserves annex uploads", async () => {
    const { isAllowedPostPath } = await import("./allowlist");

    expect(
      isAllowedPostPath(
        "/operator/procurement/tenders/1234-5-LE26/annex-bundle/preview",
      ),
    ).toBe(true);

    expect(
      isAllowedPostPath(
        "/operator/procurement/tenders/1234-5-LE26/annex-bundle/import",
      ),
    ).toBe(true);

    expect(
      isAllowedPostPath("/operations/activities"),
    ).toBe(true);
  });
});


describe("commercial operations GET readback allowlist", () => {
  it("admits only exact per-opportunity readback paths", async () => {
    const { isAllowedUpstreamPath } = await import("./allowlist");
    const opportunityId = `o_${"a".repeat(32)}`;

    for (const suffix of [
      "state",
      "activities",
      "tasks",
    ]) {
      expect(
        isAllowedUpstreamPath(
          `/operations/opportunities/${opportunityId}/${suffix}`,
        ),
        suffix,
      ).toBe(true);
    }

    expect(
      isAllowedUpstreamPath(
        "/operations/opportunities/opp_1/state",
      ),
    ).toBe(false);

    expect(
      isAllowedUpstreamPath("/operations/tasks"),
    ).toBe(false);
  });
});

describe("commercial work queue GET allowlist", () => {
  it("admits the exact global work queue path only", async () => {
    const { isAllowedUpstreamPath } =
      await import("./allowlist");

    expect(
      isAllowedUpstreamPath(
        "/operations/work-queue",
      ),
    ).toBe(true);

    expect(
      isAllowedUpstreamPath(
        "/operations/work-queue/delete",
      ),
    ).toBe(false);
  });
});

describe("CRM sales opportunity allowlist", () => {
  const salesOpportunityId = `sales_${"c".repeat(32)}`;

  it("allows the exact CRM sales-opportunity GET path", () => {
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesOpportunityId}`,
      ),
    ).toBe(true);
  });

  it("allows only the exact CRM promotion POST path", async () => {
    const {
      isAllowedCommercialOperationsPostPath,
      isAllowedPostPath,
    } = await import("./allowlist");

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/sales-opportunities/promote",
      ),
    ).toBe(true);

    expect(
      isAllowedPostPath(
        "/operations/sales-opportunities/promote",
      ),
    ).toBe(true);
  });

  it("rejects broadened or malformed CRM sales-opportunity paths", async () => {
    const {
      isAllowedCommercialOperationsPostPath,
    } = await import("./allowlist");

    expect(
      isAllowedUpstreamPath(
        "/operations/sales-opportunities/sales_short",
      ),
    ).toBe(false);

    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesOpportunityId}/extra`,
      ),
    ).toBe(false);

    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/sales_${"G".repeat(32)}`,
      ),
    ).toBe(false);

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/sales-opportunities",
      ),
    ).toBe(false);

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/sales-opportunities/promote/extra",
      ),
    ).toBe(false);
  });
});


describe("CRM-2 sales opportunity stage POST allowlist", () => {
  const salesOpportunityId = `sales_${"d".repeat(32)}`;

  it("admits the exact lifecycle stage POST path", async () => {
    const {
      isAllowedCommercialOperationsPostPath,
      isAllowedPostPath,
    } = await import("./allowlist");

    const path =
      `/operations/sales-opportunities/${salesOpportunityId}/stage`;

    expect(
      isAllowedCommercialOperationsPostPath(path),
    ).toBe(true);

    expect(
      isAllowedPostPath(path),
    ).toBe(true);

    expect(
      isAllowedCommercialOperationsPostPath(
        `${path}?request_id=test`,
      ),
    ).toBe(true);
  });

  it("does not make the lifecycle command path GET-readable", () => {
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesOpportunityId}/stage`,
      ),
    ).toBe(false);
  });

  it("rejects malformed or broadened lifecycle stage paths", async () => {
    const {
      isAllowedCommercialOperationsPostPath,
    } = await import("./allowlist");

    const rejected = [
      "/operations/sales-opportunities/sales_short/stage",
      `/operations/sales-opportunities/sales_${"G".repeat(32)}/stage`,
      `/operations/sales-opportunities/${salesOpportunityId}`,
      `/operations/sales-opportunities/${salesOpportunityId}/stage/extra`,
      `/operations/sales-opportunities/${salesOpportunityId}/delete`,
      `/operations/sales-opportunities/${salesOpportunityId}/reopen`,
    ];

    for (const path of rejected) {
      expect(
        isAllowedCommercialOperationsPostPath(path),
        path,
      ).toBe(false);
    }
  });
});

describe("CRM-Q1 customer quote allowlist", () => {
  const salesId = "sales_0123456789abcdef0123456789abcdef";
  const quoteId = "quote_0123456789abcdef0123456789abcdef";

  it("allows the exact quote list GET and detail GET paths", () => {
    expect(
      isAllowedUpstreamPath(`/operations/sales-opportunities/${salesId}/quotes`),
    ).toBe(true);
    expect(isAllowedUpstreamPath(`/operations/customer-quotes/${quoteId}`)).toBe(
      true,
    );
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/quotes?limit=20`,
      ),
    ).toBe(true);
  });

  it("allows only the exact quote-create and drive-workspace POST paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/sales-opportunities/${salesId}/quotes`,
      ),
    ).toBe(true);
    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/customer-quotes/${quoteId}/drive-workspace`,
      ),
    ).toBe(true);
  });

  it("rejects malformed or broadened quote paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    // The bare collection root is now the CRM backend foundation global
    // list route (see "manual sales-opportunity creation + global
    // customer-quote list allowlist" below) -- only the trailing-slash
    // variant stays closed.
    expect(isAllowedUpstreamPath("/operations/customer-quotes/")).toBe(false);

    // Malformed IDs.
    expect(isAllowedUpstreamPath("/operations/customer-quotes/not-an-id")).toBe(
      false,
    );
    expect(isAllowedUpstreamPath("/operations/customer-quotes/quote_short")).toBe(
      false,
    );
    expect(
      isAllowedUpstreamPath(
        "/operations/customer-quotes/quote_0123456789ABCDEF0123456789ABCDEF",
      ),
    ).toBe(false);
    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/sales-opportunities/not-an-id/quotes",
      ),
    ).toBe(false);

    // Deeper segments beyond the enumerated shapes.
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/quotes/extra`,
      ),
    ).toBe(false);
    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/customer-quotes/${quoteId}/drive-workspace/extra`,
      ),
    ).toBe(false);
    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/customer-quotes/${quoteId}/delete`,
      ),
    ).toBe(false);
  });

  it("does not make the drive-workspace command path GET-readable", () => {
    expect(
      isAllowedUpstreamPath(
        `/operations/customer-quotes/${quoteId}/drive-workspace`,
      ),
    ).toBe(false);
  });
});

describe("manual sales-opportunity creation + global customer-quote list allowlist", () => {
  it("allows the exact global customer-quote list GET path", () => {
    expect(isAllowedUpstreamPath("/operations/customer-quotes")).toBe(true);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes?stage=quoting&limit=25"),
    ).toBe(true);
  });

  it("rejects malformed or broadened global list paths", () => {
    expect(isAllowedUpstreamPath("/operations/customer-quotes/")).toBe(false);
    expect(isAllowedUpstreamPath("/operations/customer-quotes/extra")).toBe(false);
    expect(isAllowedUpstreamPath("/operations/Customer-Quotes")).toBe(false);
  });

  it("allows only the exact manual sales-opportunity POST path", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath("/operations/sales-opportunities/manual"),
    ).toBe(true);
  });

  it("rejects malformed or broadened manual-create paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath("/operations/sales-opportunities/manual/"),
    ).toBe(false);
    expect(
      isAllowedCommercialOperationsPostPath("/operations/sales-opportunities/manual/extra"),
    ).toBe(false);
  });

  it("does not make the manual-create command path GET-readable", () => {
    expect(isAllowedUpstreamPath("/operations/sales-opportunities/manual")).toBe(false);
  });

  it("does not make the global customer-quote list path POST-writable", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(isAllowedCommercialOperationsPostPath("/operations/customer-quotes")).toBe(
      false,
    );
  });
});

describe("Drive Pendientes read-only projection allowlist (CRM-Q1D follow-up)", () => {
  it("allows the exact GET path", () => {
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending"),
    ).toBe(true);
  });

  it("rejects malformed or broadened variants", () => {
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/"),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/extra"),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/Drive-Pending"),
    ).toBe(false);
  });

  it("does not make it POST-writable", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/customer-quotes/drive-pending",
      ),
    ).toBe(false);
  });
});

describe("Intake resolution read-only allowlist (CRM-Q2B)", () => {
  it("allows the exact GET path", () => {
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/resolve"),
    ).toBe(true);
  });

  it("allows it with a folder_name query string (query is stripped before matching)", () => {
    expect(
      isAllowedUpstreamPath(
        "/operations/customer-quotes/drive-pending/resolve?folder_name=CN01191-ICN%20Chile",
      ),
    ).toBe(true);
  });

  it("rejects malformed or broadened variants", () => {
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/resolve/"),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/resolve/extra"),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/drive-pending/Resolve"),
    ).toBe(false);
  });

  it("does not make it POST-writable", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/customer-quotes/drive-pending/resolve",
      ),
    ).toBe(false);
  });
});

describe("CRM-Q2 workflow/adoption allowlist", () => {
  const salesId = "sales_0123456789abcdef0123456789abcdef";
  const quoteId = "quote_0123456789abcdef0123456789abcdef";

  const transitionSegments = [
    "submit-for-review",
    "request-adjustments",
    "approve",
    "confirm-send",
    "close",
  ];

  it("allows the exact revision-transition POST paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    for (const segment of transitionSegments) {
      expect(
        isAllowedCommercialOperationsPostPath(
          `/operations/customer-quotes/${quoteId}/${segment}`,
        ),
      ).toBe(true);
    }
  });

  it("does not make the transition command paths GET-readable", () => {
    for (const segment of transitionSegments) {
      expect(
        isAllowedUpstreamPath(
          `/operations/customer-quotes/${quoteId}/${segment}`,
        ),
      ).toBe(false);
    }
  });

  it("rejects malformed quote IDs and extra segments on transition paths", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    for (const segment of transitionSegments) {
      expect(
        isAllowedCommercialOperationsPostPath(
          `/operations/customer-quotes/not-an-id/${segment}`,
        ),
      ).toBe(false);
      expect(
        isAllowedCommercialOperationsPostPath(
          `/operations/customer-quotes/${quoteId}/${segment}/extra`,
        ),
      ).toBe(false);
    }
  });

  it("allows the exact adopt-drive-folder POST path", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/sales-opportunities/${salesId}/quotes/adopt-drive-folder`,
      ),
    ).toBe(true);
  });

  it("does not make the adopt-drive-folder path GET-readable", () => {
    expect(
      isAllowedUpstreamPath(
        `/operations/sales-opportunities/${salesId}/quotes/adopt-drive-folder`,
      ),
    ).toBe(false);
  });

  it("rejects malformed sales-opportunity IDs and extra segments on adopt-drive-folder", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        "/operations/sales-opportunities/not-an-id/quotes/adopt-drive-folder",
      ),
    ).toBe(false);
    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/sales-opportunities/${salesId}/quotes/adopt-drive-folder/extra`,
      ),
    ).toBe(false);
    // Never lets adoption ride the plain quote-create path or vice versa.
    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/sales-opportunities/${salesId}/quotes`,
      ),
    ).toBe(true);
  });

  it("allows the exact event-history GET path and rejects malformed/extended variants", () => {
    expect(
      isAllowedUpstreamPath(`/operations/customer-quotes/${quoteId}/events`),
    ).toBe(true);
    expect(
      isAllowedUpstreamPath("/operations/customer-quotes/not-an-id/events"),
    ).toBe(false);
    expect(
      isAllowedUpstreamPath(
        `/operations/customer-quotes/${quoteId}/events/extra`,
      ),
    ).toBe(false);
  });

  it("does not make the event-history path POST-writable", async () => {
    const { isAllowedCommercialOperationsPostPath } = await import(
      "../src/allowlist"
    );

    expect(
      isAllowedCommercialOperationsPostPath(
        `/operations/customer-quotes/${quoteId}/events`,
      ),
    ).toBe(false);
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

  it("keeps the eleven real V2 command routes unreachable through this Worker", async () => {
    // The command boundary EXISTS in apps/api: POST /v2/commands/* records durable human
    // decisions -- five about staged evidence, and six about a commercial case, which now
    // include opening one, naming who is asking, and moving it through its stages. Building
    // that boundary and letting a browser reach it are two separate decisions, and only the
    // first has been taken. Until the second is taken deliberately, the Worker forwards
    // neither the method nor the path -- so the operator workspace stays a preview by
    // construction rather than by discipline.
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
