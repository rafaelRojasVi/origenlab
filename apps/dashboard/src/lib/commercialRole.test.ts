import { describe, expect, it } from "vitest";

import {
  COMMERCIAL_ROLE_WRITES_NOTHING,
  SUPPLIER_BRAND_NAMES,
  commercialRoleOf,
  commercialRoleProvenance,
  isSupplierBrand,
} from "./commercialRole";

const HIELSCHER = "Hielscher Ultrasonics";
const UACH = "Universidad Austral de Chile";

describe("the approved supplier brands", () => {
  it("is the closed list of six, spelled as the business spells them", () => {
    // Drift against `apps/web/src/data/brands.ts` is caught by
    // `apps/web/scripts/validate-brands.mjs`, which reads THIS file and fails if the two
    // lists disagree — Vite denies a test here from reading outside the dashboard root, and
    // the closed list belongs to the validator that already owns it. What is checked here
    // is the shape this module guarantees on its own.
    expect(SUPPLIER_BRAND_NAMES).toHaveLength(6);
    expect(new Set(SUPPLIER_BRAND_NAMES).size).toBe(6);
    expect(SUPPLIER_BRAND_NAMES).toContain("Hielscher Ultrasonics");
    expect(SUPPLIER_BRAND_NAMES.every((name) => name.trim() === name && name.length > 0)).toBe(
      true,
    );
  });

  it("matches a brand by its asserted spelling, ignoring case and spacing only", () => {
    expect(isSupplierBrand(HIELSCHER)).toBe(true);
    expect(isSupplierBrand("hielscher ultrasonics")).toBe(true);
    expect(isSupplierBrand("  Hielscher   Ultrasonics ")).toBe(true);
    // Not a fuzzy match: a different name is a different organization, here as everywhere.
    expect(isSupplierBrand("Hielscher")).toBe(false);
    expect(isSupplierBrand("Hielscher Ultrasonics GmbH")).toBe(false);
    expect(isSupplierBrand(UACH)).toBe(false);
  });
});

describe("reading a commercial role from a message", () => {
  const both = [HIELSCHER, UACH];

  it("calls an approved brand a supplier, and says the fact is the business's", () => {
    expect(commercialRoleOf(HIELSCHER, both)).toBe("supplier");
    expect(commercialRoleProvenance("supplier")).toContain("Marca aprobada");
    expect(commercialRoleProvenance("supplier")).toContain("no una inferencia");
  });

  it("reads the other institution as the one asking, and says it is a reading", () => {
    expect(commercialRoleOf(UACH, both)).toBe("requesting");
    expect(commercialRoleProvenance("requesting")).toContain("Inferido");
    expect(commercialRoleProvenance("requesting")).toContain("no un dato registrado");
  });

  it("says nothing when the message names no supplier brand at all", () => {
    // Silence rather than a guess: with no brand named there is nothing to read from.
    expect(commercialRoleOf(UACH, [UACH, "Corteva Agriscience"])).toBe("unknown");
    expect(commercialRoleOf("Corteva Agriscience", ["Corteva Agriscience"])).toBe("unknown");
  });

  it("never reads a supplier as the one asking, whatever else the message names", () => {
    expect(commercialRoleOf(HIELSCHER, [HIELSCHER])).toBe("supplier");
    expect(commercialRoleOf(HIELSCHER, [HIELSCHER, "Ortoalresa"])).toBe("supplier");
  });
});

describe("what a role annotation refuses to do", () => {
  it("names the three things it does not write", () => {
    const said = COMMERCIAL_ROLE_WRITES_NOTHING.join(" ").toLowerCase();
    expect(said).toContain("crm.organization_relationship no tiene comando");
    expect(said).toContain("no abre prospecto ni oportunidad");
    expect(said).toContain("no otorga permiso de marketing");
  });

  it("leaves the person refusal to the preview, which says it precisely", () => {
    // A shared line would flatten "creates no person" and "claims nothing about who owns
    // this address" into one sentence; they are different promises.
    expect(COMMERCIAL_ROLE_WRITES_NOTHING.join(" ")).not.toContain("persona");
  });
});
