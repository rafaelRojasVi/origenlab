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

  it("keeps representative write and non-dashboard paths blocked", () => {
    expect(isAllowedUpstreamPath("/emails")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/send")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/queues/not_a_real_queue")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/send")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/institutions/id/extra")).toBe(false);
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/id/extra")).toBe(false);
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
    // Deeper/extra path segments beyond the single tender_code segment.
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26/extra")).toBe(false);
  });
});

describe("isAllowedPostUploadPath", () => {
  it("accepts the exact annex-bundle preview path for a well-formed tender code", () => {
    expect(isAllowedPostUploadPath("/operator/procurement/tenders/745712-19-LP26/annex-bundle/preview")).toBe(
      true,
    );
  });

  it("is independent of isAllowedUpstreamPath: the plain tender path is never POST-legal", () => {
    expect(isAllowedUpstreamPath("/operator/procurement/tenders/745712-19-LP26")).toBe(true);
    expect(isAllowedPostUploadPath("/operator/procurement/tenders/745712-19-LP26")).toBe(false);
  });

  it("rejects every other allowlisted GET path", () => {
    expect(isAllowedPostUploadPath("/operator/procurement/status")).toBe(false);
    expect(isAllowedPostUploadPath("/operator/procurement/institutions")).toBe(false);
    expect(isAllowedPostUploadPath("/operator/procurement/queues/current_opportunity")).toBe(false);
    expect(isAllowedPostUploadPath("/health")).toBe(false);
  });

  it("rejects a deeper/extra path segment beyond /preview", () => {
    expect(
      isAllowedPostUploadPath("/operator/procurement/tenders/745712-19-LP26/annex-bundle/preview/extra"),
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
