import { describe, expect, it } from "vitest";
import { buildVentasDeepLinkHash, parseVentasDeepLinkOpportunityId } from "./ventasDeepLink";

const VALID_ID = "sales_" + "a".repeat(32);

describe("buildVentasDeepLinkHash", () => {
  it("builds an exact-opportunity Ventas hash", () => {
    expect(buildVentasDeepLinkHash(VALID_ID)).toBe(`#/ventas?opportunity=${VALID_ID}`);
  });
});

describe("parseVentasDeepLinkOpportunityId", () => {
  it("parses a valid opportunity id from a #/ventas hash", () => {
    expect(parseVentasDeepLinkOpportunityId(`#/ventas?opportunity=${VALID_ID}`)).toBe(VALID_ID);
  });

  it("parses a valid opportunity id from the underlying #/pipeline hash too", () => {
    expect(parseVentasDeepLinkOpportunityId(`#/pipeline?opportunity=${VALID_ID}`)).toBe(VALID_ID);
  });

  it("returns null when there is no query string", () => {
    expect(parseVentasDeepLinkOpportunityId("#/ventas")).toBeNull();
  });

  it("returns null for a different section entirely", () => {
    expect(parseVentasDeepLinkOpportunityId(`#/cotizaciones?opportunity=${VALID_ID}`)).toBeNull();
  });

  it("returns null for a malformed id (safety: never crashes on garbage input)", () => {
    expect(parseVentasDeepLinkOpportunityId("#/ventas?opportunity=not-an-id")).toBeNull();
    expect(parseVentasDeepLinkOpportunityId("#/ventas?opportunity=")).toBeNull();
    expect(
      parseVentasDeepLinkOpportunityId("#/ventas?opportunity=<script>alert(1)</script>"),
    ).toBeNull();
  });
});
