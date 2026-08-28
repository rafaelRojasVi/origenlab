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
