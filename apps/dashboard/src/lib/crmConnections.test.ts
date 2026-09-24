import { describe, expect, it } from "vitest";

import {
  caseQuoteLine,
  connectedInterestHeadline,
  connectedQuoteLine,
  contactIdentityOf,
  groupParticipants,
  organizationInitials,
  organizationRoleLabel,
  participantRoleLabel,
  withCardFacts,
} from "./crmConnections";

describe("contactIdentityOf", () => {
  it("reads only the two foreign keys", () => {
    expect(contactIdentityOf({ person_id: "p", organization_id: "o" })).toBe("person");
    expect(contactIdentityOf({ person_id: "p", organization_id: null })).toBe("person");
    expect(contactIdentityOf({ person_id: null, organization_id: "o" })).toBe(
      "organization_mailbox",
    );
    expect(contactIdentityOf({ person_id: null, organization_id: null })).toBe("unattributed");
  });
});

describe("organizationInitials", () => {
  it("skips short connector words", () => {
    expect(organizationInitials("Universidad Austral de Chile")).toBe("UA");
    expect(organizationInitials("Instituto de Salud Pública")).toBe("IS");
  });

  it("falls back to short words and to a dot", () => {
    expect(organizationInitials("5M S.A.")).toBe("5S");
    expect(organizationInitials("123")).toBe("1");
    expect(organizationInitials("—")).toBe("·");
  });
});

describe("groupParticipants", () => {
  it("keeps exact roles, in role order, with mentioned last", () => {
    const groups = groupParticipants([
      { organization_id: "m", name: "Mencionada SA", role: "mentioned", confirmation: "machine_proposed" },
      { organization_id: "s", name: "Proveedor SA", role: "supplier", confirmation: "confirmed" },
      { organization_id: "s", name: "Proveedor SA", role: "manufacturer", confirmation: "confirmed" },
      { organization_id: "r", name: "Universidad", role: "requesting_institution", confirmation: "confirmed" },
    ]);
    expect(groups.map((group) => group.role)).toEqual([
      "requesting_institution",
      "supplier",
      "manufacturer",
      "mentioned",
    ]);
    expect(groups[1]!.organizations.map((row) => row.name)).toEqual(["Proveedor SA"]);
  });
});

describe("role labels", () => {
  it("keeps a person's part apart from an institution's", () => {
    expect(participantRoleLabel("technical")).toBe("Contacto técnico");
    expect(organizationRoleLabel("supplier")).toBe("Proveedor");
    expect(organizationRoleLabel("something_new")).toBe("something_new");
  });
});

describe("quote lines", () => {
  it("says not recorded yet instead of none", () => {
    expect(caseQuoteLine(0, null)).toBe("Sin cotización registrada todavía");
    expect(caseQuoteLine(null, null)).toBeNull();
    expect(caseQuoteLine(2, "sent")).toBe("2 cotizaciones · última: Enviada");
  });

  it("reads the latest revision", () => {
    expect(
      connectedQuoteLine({
        quote_id: "q",
        quote_number: "1235",
        opportunity_id: "op",
        opportunity_title: "Caso",
        latest_revision_no: 2,
        latest_status: "draft",
        quote_currency: null,
        grand_total: null,
        valid_until: null,
        sent_at: null,
        revision_count: 2,
        updated_at: null,
      }),
    ).toBe("1235 · rev. 2 · Borrador");
  });
});

describe("connectedInterestHeadline", () => {
  it("names product, model and maker without repeating", () => {
    expect(
      connectedInterestHeadline({
        opportunity_interest_id: "i",
        opportunity_id: "op",
        opportunity_title: "Caso",
        product_id: "p",
        product_model_number: "CX-1",
        product_name: "Centrífuga",
        manufacturer_organization_id: "m",
        manufacturer_organization_name: "Fabricante",
        model_text: "CX-1 230V",
        description: null,
        quantity: 2,
        quantity_unit: null,
        confirmation: "machine_proposed",
        created_at: null,
      }),
    ).toBe("Centrífuga · CX-1 230V · Fabricante");
  });
});

describe("withCardFacts", () => {
  const row = {
    opportunity_id: "op-1",
    quote_count: null,
    latest_quote_status: null,
    last_activity_at: null,
  } as unknown as import("../api/v2Types").V2CommercialCase;
  const quote = {
    quote_id: "q",
    quote_number: "1",
    opportunity_id: "op-1",
    opportunity_title: "",
    latest_revision_no: 1,
    latest_status: "draft",
    quote_currency: null,
    grand_total: null,
    valid_until: null,
    sent_at: null,
    revision_count: 1,
    updated_at: "2026-09-01T00:00:00Z",
  };
  const summary = {
    cases: 1,
    open_cases: 1,
    interests: 0,
    quotes: 1,
    activities: 0,
    case_evidence: 0,
    last_activity_at: null,
    confirmed_people: null,
  };

  it("copies quote facts from a complete card list", () => {
    const next = withCardFacts(row, { quotes: [quote], activities: [], connection_summary: summary });
    expect(next.quote_count).toBe(1);
    expect(next.latest_quote_status).toBe("draft");
  });

  it("refuses to claim zero from a capped list", () => {
    const next = withCardFacts(row, {
      quotes: [],
      activities: [],
      connection_summary: { ...summary, quotes: 250 },
    });
    expect(next.quote_count).toBeNull();
  });
});
