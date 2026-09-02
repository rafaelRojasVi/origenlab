import { describe, expect, it } from "vitest";
import {
  customerQuoteApprovePath,
  customerQuoteConfirmSendPath,
  customerQuoteDriveWorkspacePath,
  customerQuoteEventsPath,
  customerQuotePath,
  customerQuoteRequestAdjustmentsPath,
  customerQuoteSubmitForReviewPath,
  drivePendingQuotesPath,
  salesOpportunityAdoptDriveFolderPath,
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

  it("exposes the exact Drive Pendientes projection path", () => {
    expect(drivePendingQuotesPath()).toBe(
      "/operations/customer-quotes/drive-pending",
    );
  });
});

describe("CRM-Q2 workflow/adoption client paths", () => {
  it("builds the exact revision-transition command paths", () => {
    expect(customerQuoteSubmitForReviewPath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/submit-for-review`,
    );
    expect(customerQuoteRequestAdjustmentsPath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/request-adjustments`,
    );
    expect(customerQuoteApprovePath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/approve`,
    );
    expect(customerQuoteConfirmSendPath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/confirm-send`,
    );
  });

  it("builds the exact event-history path", () => {
    expect(customerQuoteEventsPath(QUOTE_ID)).toBe(
      `/operations/customer-quotes/${QUOTE_ID}/events`,
    );
  });

  it("builds the exact adopt-drive-folder path", () => {
    expect(salesOpportunityAdoptDriveFolderPath(SALES_ID)).toBe(
      `/operations/sales-opportunities/${SALES_ID}/quotes/adopt-drive-folder`,
    );
  });

  it("rejects malformed IDs on every new path builder", () => {
    expect(() => customerQuoteSubmitForReviewPath("not-an-id")).toThrow();
    expect(() => customerQuoteRequestAdjustmentsPath("not-an-id")).toThrow();
    expect(() => customerQuoteApprovePath("not-an-id")).toThrow();
    expect(() => customerQuoteConfirmSendPath("not-an-id")).toThrow();
    expect(() => customerQuoteEventsPath("not-an-id")).toThrow();
    expect(() => salesOpportunityAdoptDriveFolderPath("not-an-id")).toThrow();
  });
});
