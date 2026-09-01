import { describe, expect, it } from "vitest";
import {
  DASHBOARD_NAV_ITEMS,
  DASHBOARD_TOP_NAV_ITEMS,
  DASHBOARD_TOP_NAV_IDS,
  dashboardSectionLabel,
} from "./dashboardNav";

describe("dashboardNav", () => {
  it("exposes exactly the 9-item flat V2 IA, in order", () => {
    expect(DASHBOARD_TOP_NAV_ITEMS.map((item) => item.label)).toEqual([
      "Inicio",
      "Ventas",
      "Cotizaciones",
      "Clientes",
      "Licitaciones",
      "Proveedores",
      "Pagos y logística",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("does not surface removed legacy sections as top-level nav items", () => {
    const topIds = new Set(DASHBOARD_TOP_NAV_IDS as readonly string[]);
    expect(topIds.has("inbox")).toBe(false);
    expect(topIds.has("deals")).toBe(false);
    expect(topIds.has("prospectos")).toBe(false);
  });

  it("keeps hidden sections resolvable by id for deep links", () => {
    expect(dashboardSectionLabel("inbox")).toBe("Bandeja de revisión");
    expect(dashboardSectionLabel("deals")).toBe("Negocios");
    expect(dashboardSectionLabel("prospectos")).toBe("Prospectos");
  });

  it("has no duplicate ids across the full registry", () => {
    const ids = DASHBOARD_NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("maps section labels", () => {
    expect(dashboardSectionLabel("today")).toBe("Inicio");
    expect(dashboardSectionLabel("pipeline")).toBe("Ventas");
    expect(dashboardSectionLabel("cotizaciones")).toBe("Cotizaciones");
    expect(dashboardSectionLabel("system")).toBe("Sistema");
  });

  it("the removed dev-only 'intel-preview' section stays out of the nav", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => (item.id as string) === "intel-preview")).toBe(false);
  });
});
