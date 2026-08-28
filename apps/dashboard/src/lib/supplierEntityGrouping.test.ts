import { describe, expect, it } from "vitest";
import type { WarmCaseItem } from "../api/commercialTypes";
import {
  groupSupplierWarmCases,
  resolveSupplierGroupId,
  roleBadgeForCategory,
} from "./supplierEntityGrouping";

function row(
  email: string,
  category: WarmCaseItem["category"],
  account = "",
  subject = "",
  lastSeen: string | null = null,
): WarmCaseItem {
  return {
    case_id: email,
    last_email_id: 1,
    last_seen_at: lastSeen,
    account_name: account,
    contact_email: email,
    subject,
    category,
    status: "open",
    next_action: "",
    equipment_signal: "",
    snippet: "",
    gmail_url: null,
  };
}

describe("supplierEntityGrouping", () => {
  it("groups suppliers by evidence domain with derived labels", () => {
    const groups = groupSupplierWarmCases([
      row("a@serva.de", "supplier_followup", "SERVA", "SERVA thread", "2026-05-20T10:00:00-04:00"),
      row("b@ika.net.br", "supplier_quote_received", "IKA", "IKA quote", "2026-05-19T10:00:00-04:00"),
      row("c@ortoalresa.com", "supplier_quote_received"),
    ]);
    expect(new Set(groups.map((g) => g.label))).toEqual(
      new Set(["SERVA", "IKA", "ortoalresa.com"]),
    );
    const serva = groups.find((g) => g.label === "SERVA");
    const ika = groups.find((g) => g.label === "IKA");
    expect(serva?.summaryLabel).toBe("1 caso en espejo");
    expect(ika?.summaryLabel).toBe("1 caso en espejo");
    expect(serva?.latestSubject).toBe("SERVA thread");
    expect(ika?.roleBadge).toBe("Cotización recibida");
    expect(serva?.roleBadge).toBe("Seguimiento");
  });

  it("resolveSupplierGroupId derives the id from the contact domain", () => {
    expect(resolveSupplierGroupId(row("x@ika.net.br", "supplier_quote_received"))).toBe(
      "domain:ika.net.br",
    );
    expect(resolveSupplierGroupId(row("", "supplier_quote_received"))).toBe("other");
  });

  it("derives the label from the most frequent account name in the group", () => {
    const groups = groupSupplierWarmCases([
      row("a@hielscher.com", "supplier_reply", "Hielscher Ultrasonics", "s1"),
      row("b@hielscher.com", "supplier_reply", "Hielscher Ultrasonics", "s2"),
      row("c@hielscher.com", "supplier_reply", "Hielscher GmbH", "s3"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Hielscher Ultrasonics");
    expect(groups[0].count).toBe(3);
  });

  it("includes grouped email count in summary when present", () => {
    const groups = groupSupplierWarmCases([
      {
        ...row("b@ika.net.br", "supplier_quote_received", "IKA", "IKA quote"),
        grouped_email_count: 13,
      },
    ]);
    expect(groups[0]?.summaryLabel).toBe("1 caso en espejo · 13+ mensajes Gmail detectados");
  });

  it("roleBadgeForCategory maps quote and follow-up", () => {
    expect(roleBadgeForCategory("supplier_quote_received")).toBe("Cotización recibida");
    expect(roleBadgeForCategory("supplier_followup")).toBe("Seguimiento");
  });
});
