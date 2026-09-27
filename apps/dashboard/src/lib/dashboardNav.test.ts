import { describe, expect, it } from "vitest";
import {
  isV2ReadOnlySection,
  DASHBOARD_NAV_ITEMS,
  DASHBOARD_TOP_NAV_ITEMS,
  DASHBOARD_TOP_NAV_IDS,
  DASHBOARD_EMPHASIZED_NAV_IDS,
  DEFAULT_DASHBOARD_SECTION,
  dashboardSectionLabel,
} from "./dashboardNav";

describe("dashboardNav", () => {
  it("exposes the Cotizaciones-first primary IA, in order, with the two 360 surfaces", () => {
    expect(DASHBOARD_TOP_NAV_ITEMS.map((item) => item.label)).toEqual([
      "Cotizaciones",
      "Licitaciones",
      "Ventas",
      "Clientes",
      "Contactos",
      "Instituciones",
      "Prospectos",
      "Correos",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("keeps «Clientes» beside Contactos rather than replacing it", () => {
    // `contacts` reads the V1 lead-intel mirror and `contactos` reads the durable V2 core.
    // They hold different rows until the V1 durable migration lands, so retiring the first
    // for the second would silently drop data an operator is relying on today.
    const topIds = new Set(DASHBOARD_TOP_NAV_IDS as readonly string[]);
    expect(topIds.has("contacts")).toBe(true);
    expect(topIds.has("contactos")).toBe(true);
    expect(topIds.has("instituciones")).toBe(true);
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

  it("still resolves the full 19-id registry (nothing deleted, only added)", () => {
    expect(DASHBOARD_NAV_ITEMS).toHaveLength(19);
  });

  it("keeps the case archive deep-linkable, read-only and out of the sidebar", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => item.id === "archivo")).toBe(true);
    expect((DASHBOARD_TOP_NAV_IDS as readonly string[]).includes("archivo")).toBe(false);
    expect(isV2ReadOnlySection("archivo")).toBe(true);
  });

  it("keeps the quotation import review deep-linkable, read-only and out of the sidebar", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => item.id === "importacion")).toBe(true);
    expect((DASHBOARD_TOP_NAV_IDS as readonly string[]).includes("importacion")).toBe(false);
    expect(isV2ReadOnlySection("importacion")).toBe(true);
  });

  it("keeps the commercial-case workspace deep-linkable and out of the sidebar", () => {
    // The third read-only V2 surface, under the same rule as `crm-v2` and `revision`: its
    // six commands live in apps/api and none of them is reachable through the proxy, so it
    // is a reading surface and does not take a sidebar slot.
    expect(DASHBOARD_NAV_ITEMS.some((item) => item.id === "casos")).toBe(true);
    expect(DASHBOARD_TOP_NAV_IDS).not.toContain("casos");
    expect(dashboardSectionLabel("casos")).toBe("Casos comerciales");
  });

  it("keeps the evidence review workspace deep-linkable and out of the sidebar", () => {
    // Same rule as `crm-v2`: a read-only surface does not take a slot in the eight-item
    // Cotizaciones-first sidebar, but a deep link to it must still render a real title.
    expect(DASHBOARD_NAV_ITEMS.some((item) => item.id === "revision")).toBe(true);
    expect(DASHBOARD_TOP_NAV_IDS).not.toContain("revision");
    expect(dashboardSectionLabel("revision")).toBe("Revisión de evidencia");
  });

  it("keeps the two 360 surfaces resolvable by id for deep links", () => {
    expect(dashboardSectionLabel("contactos")).toBe("Contactos");
    expect(dashboardSectionLabel("instituciones")).toBe("Instituciones");
  });

  it("keeps the V2 CRM browser out of the primary sidebar while still resolving its title", () => {
    // A deep link to a deep-link-only section must still get a correct page title rather
    // than rendering its raw id.
    expect(DASHBOARD_NAV_ITEMS.some((item) => item.id === "crm-v2")).toBe(true);
    expect(DASHBOARD_TOP_NAV_IDS).not.toContain("crm-v2");
    expect(dashboardSectionLabel("crm-v2")).toBe("CRM V2");
  });

  it("the removed dev-only 'intel-preview' section stays out of the nav", () => {
    expect(DASHBOARD_NAV_ITEMS.some((item) => (item.id as string) === "intel-preview")).toBe(false);
  });
});
