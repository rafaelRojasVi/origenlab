/** The five-area hashes (`#/hoy`, `#/ventas/…`, `#/finanzas/…`, `#/catalogo`, `#/ajustes/…`). Invented ids only. */
import { describe, expect, it } from "vitest";
import { areaOf, CRM_NAV, crmHash, isShellHash, legacyCrmTarget, parseCrmHash, SECTION_PATH, type CrmSection } from "./crmRoute";

const ID = "0b6c1e2a-0000-4000-8000-000000000001";

describe("section paths", () => {
  it("gives every section its place in one of the five areas", () => {
    expect(SECTION_PATH).toEqual({
      resumen: "hoy",
      oportunidades: "ventas/pipeline",
      cotizador: "ventas/cotizaciones",
      organizaciones: "ventas/clientes",
      personas: "ventas/clientes/personas",
      prospectos: "ventas/listas",
      marketing: "ventas/listas/campanas",
      licitaciones: "ventas/licitaciones",
      "fin-resumen": "finanzas/resumen",
      "fin-cobrar": "finanzas/cobrar",
      "fin-pagar": "finanzas/pagar",
      "fin-mes": "finanzas/mes",
      pagos: "finanzas/por-cobrar",
      "fin-por-pagar": "finanzas/por-pagar",
      "fin-gastos": "finanzas/gastos",
      "fin-financiamiento": "finanzas/financiamiento",
      "fin-socios": "finanzas/socios",
      catalogo: "catalogo",
      "catalogo-web": "catalogo/web",
      datos: "ajustes/operaciones",
      historial: "ajustes/historial",
      "ajustes-meta": "ajustes/meta",
    } satisfies Record<CrmSection, string>);
  });

  it("names the area of each section", () => {
    expect(areaOf("resumen")).toBe("hoy");
    expect(areaOf("oportunidades")).toBe("ventas");
    expect(areaOf("marketing")).toBe("ventas");
    expect(areaOf("pagos")).toBe("finanzas");
    expect(areaOf("catalogo-web")).toBe("catalogo");
    expect(areaOf("historial")).toBe("ajustes");
  });

  it("round-trips every section and an id", () => {
    for (const { id: section } of CRM_NAV) {
      expect(parseCrmHash(crmHash(section)), section).toEqual({ section, id: null });
    }
    expect(crmHash("oportunidades", ID)).toBe(`#/ventas/pipeline/${ID}`);
    expect(parseCrmHash(crmHash("personas", ID))).toEqual({ section: "personas", id: ID });
    expect(parseCrmHash(crmHash("organizaciones", ID))).toEqual({ section: "organizaciones", id: ID });
    expect(parseCrmHash(crmHash("datos", null, "proveedores"))).toEqual({ section: "datos", id: null, tab: "proveedores" });
    expect(crmHash("datos", null, "drive")).toBe("#/ajustes/operaciones/drive");
    expect(crmHash("drive")).toBe("#/ajustes/operaciones/drive");
    expect(crmHash("proveedores")).toBe("#/ajustes/operaciones/proveedores");
    expect(parseCrmHash("#/catalogo/web")).toEqual({ section: "catalogo-web", id: null });
    expect(parseCrmHash("#/catalogo/web/up400st")).toEqual({ section: "catalogo-web", id: "up400st" });
    expect(parseCrmHash(`#/ventas/licitaciones/mp-${ID}`).id).toBe(`mp-${ID}`);
    expect(parseCrmHash("#/ventas/cotizaciones/rentabilidad")).toEqual({ section: "cotizador", id: null, pane: "rentabilidad" });
  });

  it("reads the optional first tab of Clientes and Listas", () => {
    expect(parseCrmHash("#/ventas/clientes/organizaciones")).toEqual({ section: "organizaciones", id: null });
    expect(parseCrmHash(`#/ventas/clientes/organizaciones/${ID}`)).toEqual({ section: "organizaciones", id: ID });
    expect(parseCrmHash("#/ventas/listas/prospectos")).toEqual({ section: "prospectos", id: null });
    expect(parseCrmHash(`#/ventas/listas/prospectos/${ID}`)).toEqual({ section: "prospectos", id: ID });
  });

  it("keeps a page's own query out of the route", () => {
    expect(parseCrmHash("#/catalogo?s=x&q=y")).toEqual({ section: "catalogo", id: null });
    expect(parseCrmHash(`#/catalogo/${ID}?q=y`)).toEqual({ section: "catalogo", id: ID });
  });

  it("drops ids that are not UUIDs, and reads old #/crm hashes as nothing", () => {
    expect(parseCrmHash("#/ventas/pipeline/../../v2/commands")).toEqual({ section: "oportunidades", id: null });
    expect(parseCrmHash(`#/ventas/pipeline/mp-${ID}`)).toEqual({ section: "oportunidades", id: null });
    expect(parseCrmHash("#/ventas/licitaciones/mp-1234567-12-LE26")).toEqual({ section: "licitaciones", id: null });
    expect(parseCrmHash("#/catalogo/preferencias")).toEqual({ section: "catalogo", id: null });
    expect(parseCrmHash(`#/crm/oportunidades/${ID}`)).toEqual({ section: "resumen", id: null });
    expect(parseCrmHash("#/nope")).toEqual({ section: "resumen", id: null });
  });
});

describe("isShellHash", () => {
  it("recognises the five areas and nothing else", () => {
    for (const h of ["#/hoy", "#/ventas", "#/ventas/pipeline", "#/finanzas/por-cobrar", "#/catalogo?q=x", "#/ajustes/"]) {
      expect(isShellHash(h), h).toBe(true);
    }
    for (const h of ["", "#/", "#/crm", "#/crm/resumen", "#/hoyx", "#/catalogos", "#/pipeline"]) {
      expect(isShellHash(h), h).toBe(false);
    }
  });
});

describe("legacyCrmTarget", () => {
  it("moves an old #/crm address to its new hash, keeping id, tab, pane and query", () => {
    expect(legacyCrmTarget("#/crm")).toBe("#/hoy");
    expect(legacyCrmTarget("#/crm/nope")).toBe("#/hoy");
    expect(legacyCrmTarget(`#/crm/oportunidades/${ID.toUpperCase()}`)).toBe(`#/ventas/pipeline/${ID}`);
    expect(legacyCrmTarget(`#/crm/cotizar/${ID}`)).toBe(`#/ventas/cotizaciones/${ID}`);
    expect(legacyCrmTarget("#/crm/cotizar/nope")).toBe("#/ventas/cotizaciones");
    expect(legacyCrmTarget("#/crm/cotizador/preferencias")).toBe("#/ventas/cotizaciones/preferencias");
    expect(legacyCrmTarget(`#/crm/organizaciones/${ID}`)).toBe(`#/ventas/clientes/${ID}`);
    expect(legacyCrmTarget(`#/crm/prospectos/${ID}`)).toBe(`#/ventas/listas/${ID}`);
    expect(legacyCrmTarget(`#/crm/licitaciones/mp-${ID}`)).toBe(`#/ventas/licitaciones/mp-${ID}`);
    expect(legacyCrmTarget(`#/crm/catalogo/${ID}?q=y`)).toBe(`#/catalogo/${ID}?q=y`);
    expect(legacyCrmTarget("#/crm/proveedores")).toBe("#/ajustes/operaciones/proveedores");
    expect(legacyCrmTarget("#/crm/datos")).toBe("#/ajustes/operaciones");
    expect(legacyCrmTarget("#/crm/historial")).toBe("#/ajustes/historial");
  });

  it("leaves everything that is not an old #/crm address alone", () => {
    for (const h of ["", "#/crmx", "#/hoy", "#/ventas/pipeline", "#/pipeline"]) expect(legacyCrmTarget(h), h).toBeNull();
  });
});
