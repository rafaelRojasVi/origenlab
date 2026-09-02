import { describe, expect, it } from "vitest";
import {
  DASHBOARD_NAV_ITEMS,
  DASHBOARD_TOP_NAV_ITEMS,
  DASHBOARD_TOP_NAV_IDS,
  DASHBOARD_EMPHASIZED_NAV_IDS,
  DEFAULT_DASHBOARD_SECTION,
  dashboardSectionLabel,
} from "./dashboardNav";

describe("dashboardNav", () => {
  it("exposes exactly the 8-item Cotizaciones-first primary IA, in order", () => {
    expect(DASHBOARD_TOP_NAV_ITEMS.map((item) => item.label)).toEqual([
      "Cotizaciones",
      "Licitaciones",
      "Ventas",
      "Clientes",
      "Prospectos",
      "Correos",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("defaults to Cotizaciones as the landing section", () => {
    expect(DEFAULT_DASHBOARD_SECTION).toBe("cotizaciones");
  });

  it("does not surface retired primary-nav concepts as top-level nav items", () => {
    const topIds = new Set(DASHBOARD_TOP_NAV_IDS as readonly string[]);
    expect(topIds.has("today")).toBe(false);
    expect(topIds.has("deals")).toBe(false);
    expect(topIds.has("suppliers")).toBe(false);
    expect(topIds.has("payments-logistics")).toBe(false);
  });

  it("keeps hidden sections resolvable by id for deep links", () => {
    expect(dashboardSectionLabel("today")).toBe("Inicio");
    expect(dashboardSectionLabel("deals")).toBe("Negocios");
    expect(dashboardSectionLabel("suppliers")).toBe("Proveedores");
    expect(dashboardSectionLabel("payments-logistics")).toBe("Pagos y logística");
  });

  it("relabels the former Bandeja de revisión as Correos and promotes it to primary nav", () => {
    const inbox = DASHBOARD_NAV_ITEMS.find((item) => item.id === "inbox")!;
    expect(inbox.label).toBe("Correos");
    expect((DASHBOARD_TOP_NAV_IDS as readonly string[]).includes("inbox")).toBe(true);
  });

  it("emphasizes exactly Cotizaciones, Licitaciones and Ventas", () => {
    expect([...DASHBOARD_EMPHASIZED_NAV_IDS].sort()).toEqual(
      ["cotizaciones", "pipeline", "tenders"].sort(),
    );
  });

  it("has no duplicate ids across the full registry", () => {
    const ids = DASHBOARD_NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("still resolves the full 12-id registry (nothing deleted, only reordered/relabeled)", () => {
    expect(DASHBOARD_NAV_ITEMS).toHaveLength(12);
  });

  it("the removed dev-only 'intel-preview' section stays out of the nav", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => (item.id as string) === "intel-preview")).toBe(false);
  });
});
