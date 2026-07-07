import { describe, expect, it } from "vitest";

import { isAllowedUpstreamPath, stripApiPrefix } from "../src/allowlist";
import { buildUpstreamUrl } from "../src/proxy";

describe("allowlist", () => {
  const PRODUCTION_SMOKE_PATHS = [
    "/health",
    "/operator/status",
    "/operator/automation-status",
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
    expect(isAllowedUpstreamPath("/api/operator/status")).toBe(false);
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
