import { describe, expect, it } from "vitest";
import {
  customerQuoteDriveWorkspacePath,
  customerQuotePath,
  salesOpportunityQuotesPath,
} from "./customerQuoteClient";

const QUOTE_ID = `quote_${"a".repeat(32)}`;
const SALES_ID = `sales_${"b".repeat(32)}`;

describe("customer quote client paths", () => {
  it("builds exact allowlisted paths for well-formed IDs", () => {
    expect(salesOpportunityQuotesPath(SALES_ID)).toBe(
      `/operations/sales-opportunities/${SALES_ID}/quotes`,
    );
    expect(customerQuotePath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}`,
    );
    expect(customerQuoteDriveWorkspacePath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/drive-workspace`,
    );
  });

  it("rejects malformed IDs before any request is built", () => {
    expect(() => salesOpportunityQuotesPath("sales_short")).toThrow();
    expect(() => salesOpportunityQuotesPath("o_" + "a".repeat(32))).toThrow();
    expect(() => customerQuotePath("not-an-id")).toThrow();
    expect(() =>
      customerQuotePath(`quote_${"A".repeat(32)}`),
    ).toThrow();
    expect(() =>
      customerQuoteDriveWorkspacePath("quote_../../etc"),
    ).toThrow();
  });
});
