import { describe, expect, it } from "vitest";
import { pickContactEmailFromLists } from "../lib/smokeContactPick";

describe("smoke contact drilldown helpers", () => {
  it("picks email from warm cases first", () => {
    const picked = pickContactEmailFromLists({
      items: [{ contact_email: "warm@cliente.cl" }],
    });
    expect(picked).toEqual({ email: "warm@cliente.cl", source: "warm_cases" });
  });

  it("returns null when no valid email (smoke skip, not failure)", () => {
    expect(
      pickContactEmailFromLists({ items: [{ contact_email: "no-at-sign" }] }),
    ).toBeNull();
  });
});
