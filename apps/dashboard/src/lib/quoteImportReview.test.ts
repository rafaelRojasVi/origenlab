import { describe, expect, it } from "vitest";

import type { ImportOpportunity } from "../api/quoteImportReview";
import { EMPTY_IMPORT_FILTER, filterImportRows } from "./quoteImportReview";

function row(over: Partial<ImportOpportunity>): ImportOpportunity {
  return {
    planned_opportunity_id: "11111111-1111-5111-8111-111111111111",
    plan_status: "ready",
    plan_status_raw: "ready",
    reasons: [],
    warnings: [],
    printed_organization: "Universidad de Santiago de Chile",
    printed_addressee: null,
    recipient_addresses: [],
    confirmation: { state: "confirmed", organization_name: "Usach" },
    opportunity: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title: "Cotización 01185-26", stage: "quoting", version: 5 },
    organizations: [],
    quotes: [{ quote_number: "01185-26", revisions: [] }],
    evidence: [],
    checks: [],
    completeness: "complete",
    commands_planned: 7,
    receipts_in_database: 7,
    ...over,
  };
}

const waiting = row({
  planned_opportunity_id: "22222222-2222-5222-8222-222222222222",
  plan_status: "waiting",
  printed_organization: "Universidad Mayor",
  confirmation: { state: "pending" },
  opportunity: null,
  quotes: [{ quote_number: "01024-26", revisions: [] }],
  completeness: "not_imported",
});
const rows = [row({}), waiting];

describe("filterImportRows", () => {
  it("returns everything with the empty filter", () => {
    expect(filterImportRows(rows, EMPTY_IMPORT_FILTER)).toHaveLength(2);
  });
  it("matches organizations accent- and case-insensitively, including the confirmed name", () => {
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, organization: "USACH" })).toEqual([rows[0]]);
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, organization: "mayor" })).toEqual([waiting]);
  });
  it("matches a quote number by serial whatever the padding or prefix", () => {
    for (const q of ["1185", "01185-26", "CN01185"]) {
      expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, quoteNumber: q })).toEqual([rows[0]]);
    }
  });
  it("matches the planned id, the database id or the title", () => {
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, opportunity: "aaaaaaaa" })).toEqual([rows[0]]);
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, opportunity: "22222222" })).toEqual([waiting]);
  });
  it("filters by plan status and evidence completeness", () => {
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, status: "waiting" })).toEqual([waiting]);
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, completeness: "complete" })).toEqual([rows[0]]);
    expect(filterImportRows(rows, { ...EMPTY_IMPORT_FILTER, status: "ready", completeness: "not_imported" })).toEqual([]);
  });
});
