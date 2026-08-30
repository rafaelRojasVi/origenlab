import { describe, expect, it } from "vitest";
import type { WarmCaseItem } from "../api/commercialTypes";
import { computeTodaySummaryCounts } from "./todaySummaryCounts";

function row(category: WarmCaseItem["category"], email = "x@y.cl"): WarmCaseItem {
  return {
    case_id: "1",
    last_email_id: 1,
    last_seen_at: null,
    account_name: "",
    contact_email: email,
    subject: "",
    category,
    status: "open",
    next_action: "",
    equipment_signal: "",
    snippet: "",
    gmail_url: null,
  };
}

describe("todaySummaryCounts", () => {
  it("aggregates Phase 7A role buckets", () => {
    const counts = computeTodaySummaryCounts(
      [
        row("client_opportunity"),
        row("supplier_quote_received"),
        row("payment_admin"),
        row("deal_evidence_candidate"),
      ],
      3,
      [{ margin_blockers: ["fx"], client_org_name: "A", supplier_org_name: "B" } as never],
    );
    expect(counts.clientOpportunities).toBe(1);
    expect(counts.supplierQuotesFollowups).toBe(1);
    expect(counts.paymentsLogistics).toBe(1);
    expect(counts.dealEvidence).toBe(1);
    expect(counts.dealBlockers).toBe(1);
    expect(counts.actionableOpportunities).toBe(3);
  });

  it("passes the actionable-opportunity count through unchanged (availability is decided by the caller)", () => {
    const counts = computeTodaySummaryCounts([], 99, []);
    expect(counts.actionableOpportunities).toBe(99);
  });

  it("passes a healthy zero through unchanged", () => {
    const counts = computeTodaySummaryCounts([], 0, []);
    expect(counts.actionableOpportunities).toBe(0);
  });
});
