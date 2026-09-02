import { describe, expect, it } from "vitest";
import { filterQuoteQueueItems, quoteQueueStateLabel } from "./customerQuoteQueueFilters";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

describe("filterQuoteQueueItems", () => {
  it("matches on quote_number, document_number, organization and contact (case-insensitive)", () => {
    const rows = [
      globalQuoteItemFixture({
        quote: { ...globalQuoteItemFixture().quote, quote_number: "01183-26" },
        organization_display_name: "CEAF",
      }),
    ];
    expect(filterQuoteQueueItems(rows, { searchText: "ceaf", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueItems(rows, { searchText: "01183", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueItems(rows, { searchText: "nope", recency: "all" })).toHaveLength(0);
  });

  it("filters by recency against quote.updated_at", () => {
    const now = new Date("2026-09-01T00:00:00Z");
    const recent = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, updated_at: "2026-08-31T00:00:00Z" },
    });
    const old = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, updated_at: "2026-06-01T00:00:00Z" },
    });
    const result = filterQuoteQueueItems([recent, old], { searchText: "", recency: "7d" }, now);
    expect(result).toHaveLength(1);
  });
});

describe("quoteQueueStateLabel", () => {
  it.each([
    ["ready", "Drive listo"],
    ["pending", "Aprovisionando"],
    ["failed", "Error de Drive"],
  ] as const)("maps drive provisioning_status %s to %s", (status, label) => {
    const base = globalQuoteItemFixture();
    const row = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: { ...base.quote.drive_workspace, provisioning_status: status },
      },
    });
    expect(quoteQueueStateLabel(row).drive).toBe(label);
    expect(quoteQueueStateLabel(row).status).toBe("Borrador");
  });

  it("labels a pending workspace as provisioning language, never failure language", () => {
    const base = globalQuoteItemFixture();
    const row = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: { ...base.quote.drive_workspace, provisioning_status: "pending" },
      },
    });
    expect(quoteQueueStateLabel(row).drive).not.toMatch(/error|fall|falló/i);
  });
});
