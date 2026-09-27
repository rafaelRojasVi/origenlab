import { describe, expect, it } from "vitest";

import { parseV2OrganizationsPage } from "./v2Parse";

const row = {
  organization_id: "11111111-2222-4333-8444-555555555555",
  name: "Fabricante Ficticio",
  kind: "unknown",
  confirmation: "confirmed",
  case_count: 1,
  cases_as_supplier: 1,
  cases_as_manufacturer: 1,
  relationship_roles: ["supplier", 7, ""],
};

describe("parseV2OrganizationsPage", () => {
  it("reads the per-role case counts and the recorded relationship roles", () => {
    const [org] = parseV2OrganizationsPage({ items: [row], total: 1, limit: 50, offset: 0 }).items;
    expect(org!.cases_as_supplier).toBe(1);
    expect(org!.cases_as_manufacturer).toBe(1);
    expect(org!.cases_as_requesting_institution).toBe(0);
    // A non-string or blank role is dropped, never coerced into a role.
    expect(org!.relationship_roles).toEqual(["supplier"]);
  });

  it("reads the segment facets", () => {
    const parsed = parseV2OrganizationsPage({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      facets: { all: 1813, customers: 1, suppliers: 1, others: 0 },
    });
    expect(parsed.facets).toEqual({ all: 1813, customers: 1, suppliers: 1, others: 0 });
  });

  it("leaves facets unknown when the response carries none, rather than zero", () => {
    const parsed = parseV2OrganizationsPage({ items: [], total: 0, limit: 50, offset: 0 });
    expect(parsed.facets).toBeNull();
  });
});
