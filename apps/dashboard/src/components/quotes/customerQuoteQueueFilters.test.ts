import { describe, expect, it } from "vitest";
import { filterQuoteQueueRows, quoteQueueStateLabel } from "./customerQuoteQueueFilters";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../../test/fixtures/customerQuoteFixtures";
import type { QuoteQueueRow } from "../../api/customerQuoteTypes";

function crmRow(overrides: Parameters<typeof globalQuoteItemFixture>[0] = {}): QuoteQueueRow {
  return { kind: "crm", item: globalQuoteItemFixture(overrides) };
}

function driveRow(
  overrides: Parameters<typeof drivePendingQuoteItemFixture>[0] = {},
): QuoteQueueRow {
  return { kind: "drive_pending", item: drivePendingQuoteItemFixture(overrides) };
}

describe("filterQuoteQueueRows", () => {
  it("matches CRM rows on quote_number, document_number, organization and contact (case-insensitive)", () => {
    const rows = [
      crmRow({
        quote: { ...globalQuoteItemFixture().quote, quote_number: "01183-26" },
        organization_display_name: "CEAF",
      }),
    ];
    expect(filterQuoteQueueRows(rows, { searchText: "ceaf", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueRows(rows, { searchText: "01183", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueRows(rows, { searchText: "nope", recency: "all" })).toHaveLength(0);
  });

  it("filters CRM rows by recency against quote.updated_at", () => {
    const now = new Date("2026-09-01T00:00:00Z");
    const recent = crmRow({
      quote: { ...globalQuoteItemFixture().quote, updated_at: "2026-08-31T00:00:00Z" },
    });
    const old = crmRow({
      quote: { ...globalQuoteItemFixture().quote, updated_at: "2026-06-01T00:00:00Z" },
    });
    const result = filterQuoteQueueRows([recent, old], { searchText: "", recency: "7d" }, now);
    expect(result).toHaveLength(1);
  });

  it("matches Drive-only rows on folder name and document identifier", () => {
    const rows = [driveRow({ folder_name: "CN01191-ICN Chile", document_identifier: "CN01191" })];

    expect(filterQuoteQueueRows(rows, { searchText: "icn chile", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueRows(rows, { searchText: "cn01191", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueRows(rows, { searchText: "nope", recency: "all" })).toHaveLength(0);
  });

  it("filters Drive-only rows by recency against modified_time, falling back to created_time", () => {
    const now = new Date("2026-09-01T00:00:00Z");
    const recent = driveRow({ modified_time: "2026-08-31T00:00:00Z", created_time: null });
    const old = driveRow({ modified_time: "2026-06-01T00:00:00Z", created_time: null });

    const result = filterQuoteQueueRows([recent, old], { searchText: "", recency: "7d" }, now);
    expect(result).toHaveLength(1);
  });

  it("never hides a Drive-only row with no known date under a recency filter", () => {
    const now = new Date("2026-09-01T00:00:00Z");
    const undated = driveRow({ modified_time: null, created_time: null });

    const result = filterQuoteQueueRows([undated], { searchText: "", recency: "7d" }, now);
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
