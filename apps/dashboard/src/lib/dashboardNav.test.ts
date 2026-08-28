import { describe, expect, it } from "vitest";
import {
  DASHBOARD_NAV_GROUPS,
  DASHBOARD_NAV_ITEMS,
  dashboardSectionGroupLabel,
  dashboardSectionLabel,
} from "./dashboardNav";

describe("dashboardNav", () => {
  it("groups contain all sections without duplicates", () => {
    const ids = DASHBOARD_NAV_GROUPS.flatMap((group) => group.items.map((item) => item.id));
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.length).toBe(DASHBOARD_NAV_ITEMS.length);
  });

  it("maps section labels and groups", () => {
    expect(dashboardSectionLabel("today")).toBe("Hoy");
    expect(dashboardSectionGroupLabel("deals")).toBe("Comercial");
    expect(dashboardSectionGroupLabel("system")).toBe("Sistema");
  });

  it("A: the removed dev-only 'intel-preview' section stays out of the nav", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => (item.id as string) === "intel-preview")).toBe(false);
    const flattenedIds = DASHBOARD_NAV_GROUPS.flatMap((group) => group.items.map((item) => item.id as string));
    expect(flattenedIds).not.toContain("intel-preview");
  });

  it("B: real 'Prospectos' (DeepSearch/Gmail commercial prospecting) remains a normal nav item", () => {
    const item = DASHBOARD_NAV_ITEMS.find((i) => i.id === "prospectos");
    expect(item?.label).toBe("Prospectos");
  });

  it("C: real 'Licitaciones / equipos' remains a normal nav item", () => {
    const item = DASHBOARD_NAV_ITEMS.find((i) => i.id === "tenders");
    expect(item?.label).toBe("Licitaciones / equipos");
  });
});
