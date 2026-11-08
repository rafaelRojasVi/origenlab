import { describe, expect, it } from "vitest";
import { AREAS } from "./nav";
import { COTIZADOR_PREFERENCES, COTIZADOR_RENTABILIDAD, CRM_NAV, crmHash, sectionTitle } from "./crmRoute";
import { LEGACY_REDIRECTS, parseShellHash, redirectHash, SHELL_HOME_HASH } from "./shellRoute";

describe("shell route", () => {
  it("routes CRM hashes to the CRM, with a case id when it is a UUID", () => {
    const id = "6bafddfa-0000-4000-8000-000000000001";
    expect(parseShellHash(`#/crm/oportunidades/${id}`)).toEqual({ section: "oportunidades", id });
    expect(parseShellHash("#/crm")).toEqual({ section: "resumen", id: null });
    expect(parseShellHash(`#/ventas/pipeline/${id}`)).toEqual({ section: "oportunidades", id });
    expect(redirectHash(`#/ventas/pipeline/${id}`)).toBeNull();
    expect(redirectHash(`#/crm/oportunidades/${id}`)).toBe(`#/ventas/pipeline/${id}`);
  });

  it("sends the bare root and unknown hashes to Hoy", () => {
    for (const hash of ["", "#", "#/", "#/nope", "#/crmx"]) {
      expect(parseShellHash(hash), hash).toEqual({ section: "resumen", id: null });
      expect(redirectHash(hash), hash).toBe("#/hoy");
    }
  });

  it("redirects earlier-panel bookmarks to their CRM equivalent", () => {
    expect(redirectHash("#/cotizaciones")).toBe("#/ventas/pipeline");
    expect(redirectHash(`#/ventas?opportunity=sales_${"a".repeat(32)}`)).toBe("#/ventas/pipeline");
    expect(redirectHash("#/contactos")).toBe("#/ventas/clientes/personas");
    expect(redirectHash("#/instituciones")).toBe("#/ventas/clientes");
    expect(redirectHash("#/suppliers")).toBe("#/ajustes/operaciones/proveedores");
    expect(redirectHash("#/archivo")).toBe("#/ajustes/operaciones/drive");
    expect(redirectHash("#/revision")).toBe("#/ajustes/historial");
    expect(redirectHash("#/tenders")).toBe("#/hoy");
    // «catalogo» is a real route now: no redirect, same page as before.
    expect(redirectHash("#/catalogo")).toBeNull();
    expect(parseShellHash("#/catalogo")).toEqual({ section: "catalogo", id: null });
  });

  it("opens the catalog and one product by its id", () => {
    const id = "6bafddfa-0000-4000-8000-0000000000c1";
    expect(parseShellHash("#/crm/catalogo")).toEqual({ section: "catalogo", id: null });
    expect(parseShellHash(`#/crm/catalogo/${id}`)).toEqual({ section: "catalogo", id });
  });

  it("keeps the selected case from an earlier-panel bookmark", () => {
    const id = "6bafddfa-0000-4000-8000-000000000001";
    expect(redirectHash(`#/ventas?opportunity=${id}`)).toBe(`#/ventas/pipeline/${id}`);
    expect(redirectHash(`#/pipeline?opportunity=${id.toUpperCase()}`)).toBe(`#/ventas/pipeline/${id}`);
    expect(redirectHash(`#/casos?id=${id}`)).toBe(`#/ventas/pipeline/${id}`);
    expect(redirectHash(`#/cotizaciones?opportunity=${id}&tab=x`)).toBe(`#/ventas/pipeline/${id}`);
    // Not a case id, or not a section with a single-record view: the list, never a guess.
    expect(redirectHash("#/ventas?opportunity=../../x")).toBe("#/ventas/pipeline");
    expect(redirectHash(`#/contactos?id=${id}`)).toBe("#/ventas/clientes/personas");
    expect(redirectHash(`#/tenders?id=${id}`)).toBe("#/hoy");
  });

  it("covers every section of the earlier panel", () => {
    const old = [
      "today", "inbox", "pipeline", "deals", "prospectos", "cotizaciones", "suppliers", "tenders",
      "payments-logistics", "contacts", "contactos", "instituciones", "crm-v2", "revision", "casos", "importacion",
      "archivo", "system",
    ];
    for (const s of old) expect(LEGACY_REDIRECTS[s], s).toBeDefined();
    // «ventas» and «catalogo» are area routes now, not legacy paths.
    expect(LEGACY_REDIRECTS.ventas).toBeUndefined();
    expect(LEGACY_REDIRECTS.catalogo).toBeUndefined();
  });
});

describe("Cotizador route", () => {
  const id = "6bafddfa-0000-4000-8000-0000000000d1";

  it("opens the module, and a quote in it by its id, round-tripping through crmHash", () => {
    expect(parseShellHash("#/crm/cotizador")).toEqual({ section: "cotizador", id: null });
    expect(parseShellHash(`#/crm/cotizador/${id}`)).toEqual({ section: "cotizador", id });
    expect(crmHash("cotizador", id)).toBe(`#/ventas/cotizaciones/${id}`);
    expect(redirectHash(`#/ventas/cotizaciones/${id}`)).toBeNull();
  });

  it("«Preferencias» is a fixed pane of the module, never a quote id", () => {
    expect(COTIZADOR_PREFERENCES).toBe("preferencias");
    expect(parseShellHash("#/crm/cotizador/preferencias")).toEqual({ section: "cotizador", id: null, pane: "preferencias" });
    expect(parseShellHash("#/crm/cotizador/preferencias?x=1")).toEqual({ section: "cotizador", id: null, pane: "preferencias" });
    expect(crmHash("cotizador", COTIZADOR_PREFERENCES)).toBe("#/ventas/cotizaciones/preferencias");
    expect(redirectHash("#/ventas/cotizaciones/preferencias")).toBeNull();
    // Only the module has it; anything else in that place is still no id at all.
    expect(parseShellHash("#/crm/catalogo/preferencias")).toEqual({ section: "catalogo", id: null });
    expect(parseShellHash("#/crm/cotizador/otra-cosa")).toEqual({ section: "cotizador", id: null });
  });

  it("«Rentabilidad» is another fixed pane of the module", () => {
    expect(COTIZADOR_RENTABILIDAD).toBe("rentabilidad");
    expect(parseShellHash("#/crm/cotizador/rentabilidad")).toEqual({ section: "cotizador", id: null, pane: "rentabilidad" });
    expect(crmHash("cotizador", COTIZADOR_RENTABILIDAD)).toBe("#/ventas/cotizaciones/rentabilidad");
    expect(redirectHash("#/ventas/cotizaciones/rentabilidad")).toBeNull();
    expect(parseShellHash("#/crm/catalogo/rentabilidad")).toEqual({ section: "catalogo", id: null });
  });

  it("is Ventas' second tab, «Cotizaciones», for sales and admin", () => {
    const ventas = AREAS.find((a) => a.key === "ventas")!;
    expect(ventas.tabs.map((t) => t.label)).toEqual(["Pipeline", "Cotizaciones", "Clientes", "Listas y llamadas", "Licitaciones"]);
    expect(ventas.tabs[1]).toMatchObject({ sections: ["cotizador"], quoting: true });
    expect(sectionTitle("cotizador")).toBe("Cotizaciones");
    expect(CRM_NAV.find((n) => n.id === "cotizador")?.quoting).toBe(true);
  });

  it("«Licitaciones» is for every role and has a list and a page per tender", () => {
    expect(sectionTitle("licitaciones")).toBe("Licitaciones");
    const nav = CRM_NAV.find((n) => n.id === "licitaciones");
    expect(nav?.quoting).toBeUndefined();
    expect(nav?.adminOnly).toBeUndefined();
    expect(parseShellHash("#/crm/licitaciones")).toEqual({ section: "licitaciones", id: null });
    expect(parseShellHash(`#/crm/licitaciones/${id}`)).toEqual({ section: "licitaciones", id });
    expect(crmHash("licitaciones", id)).toBe(`#/ventas/licitaciones/${id}`);
    expect(redirectHash("#/ventas/licitaciones")).toBeNull();
  });

  it("redirects the old one-quote address to the module's tab", () => {
    expect(redirectHash(`#/crm/cotizar/${id}`)).toBe(`#/ventas/cotizaciones/${id}`);
    expect(redirectHash(`#/crm/cotizar/${id.toUpperCase()}`)).toBe(`#/ventas/cotizaciones/${id}`);
    expect(redirectHash("#/crm/cotizar")).toBe("#/ventas/cotizaciones");
    expect(redirectHash("#/crm/cotizar/nope")).toBe("#/ventas/cotizaciones");
    // Read before the redirect lands, it already opens the module.
    expect(parseShellHash(`#/crm/cotizar/${id}`)).toEqual({ section: "cotizador", id });
  });

  it("drops an id that is not a UUID", () => {
    expect(parseShellHash("#/crm/cotizador/nope")).toEqual({ section: "cotizador", id: null });
  });
});

describe("five-area redirects", () => {
  const id = "0b6c1e2a-0000-4000-8000-000000000001";

  it.each([
    ["#/crm/resumen", "#/hoy"],
    ["", "#/hoy"],
    [`#/crm/oportunidades/${id}`, `#/ventas/pipeline/${id}`],
    [`#/crm/cotizar/${id}`, `#/ventas/cotizaciones/${id}`],
    ["#/crm/cotizador/rentabilidad", "#/ventas/cotizaciones/rentabilidad"],
    [`#/crm/personas/${id}`, `#/ventas/clientes/personas/${id}`],
    ["#/crm/marketing", "#/ventas/listas/campanas"],
    ["#/crm/pagos", "#/finanzas/por-cobrar"],
    ["#/crm/catalogo?s=hielscher&q=up", "#/catalogo?s=hielscher&q=up"],
    ["#/crm/catalogo-web/up400st", "#/catalogo/web/up400st"],
    ["#/crm/datos/drive", "#/ajustes/operaciones/drive"],
    ["#/crm/drive", "#/ajustes/operaciones/drive"],
    ["#/crm/revision", "#/ajustes/historial"],
    [`#/pipeline?opportunity=${id}`, `#/ventas/pipeline/${id}`],
  ])("redirects %s to %s", (from, to) => expect(redirectHash(from)).toBe(to));

  it("never redirects a new hash, so there is no loop", () => {
    for (const s of CRM_NAV.map((n) => n.id)) expect(redirectHash(crmHash(s)), s).toBeNull();
    expect(redirectHash(crmHash("oportunidades", id))).toBeNull();
    expect(redirectHash(crmHash("datos", null, "drive"))).toBeNull();
    expect(redirectHash("#/catalogo?s=hielscher&q=up")).toBeNull();
    expect(redirectHash(SHELL_HOME_HASH)).toBeNull();
    expect(SHELL_HOME_HASH).toBe("#/hoy");
  });

  it("sends an area root, and a path no page of the area has, to the area's first page", () => {
    expect(redirectHash("#/ventas")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/ventas/")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/finanzas")).toBe("#/finanzas/por-cobrar");
    expect(redirectHash("#/ajustes")).toBe("#/ajustes/operaciones");
    expect(redirectHash("#/hoy")).toBeNull();
    expect(redirectHash("#/catalogo")).toBeNull();
    expect(redirectHash("#/ventas/nope")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/ventas/foo/bar")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/finanzas/mes")).toBe("#/finanzas/por-cobrar");
    expect(redirectHash("#/ajustes/perfiles")).toBe("#/ajustes/operaciones");
    // Every redirect target is itself final.
    for (const h of ["#/ventas", "#/finanzas", "#/ajustes", "#/ventas/nope", "#/finanzas/mes", "#/Ventas/Nope", "#/crm/catalogo?q=x", "#/today"]) {
      expect(redirectHash(redirectHash(h)!), h).toBeNull();
    }
  });

  it("reads an area path without regard to case, keeping the query as typed", () => {
    expect(redirectHash("#/Ventas/Pipeline")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/VENTAS")).toBe("#/ventas/pipeline");
    expect(redirectHash("#/Finanzas/Mes")).toBe("#/finanzas/por-cobrar");
    expect(redirectHash("#/HOY")).toBe("#/hoy");
    expect(redirectHash("#/Catalogo?q=UP400St")).toBe("#/catalogo?q=UP400St");
    expect(redirectHash(`#/Ventas/Clientes/Personas/${id}`)).toBe(`#/ventas/clientes/personas/${id}`);
    expect(parseShellHash("#/Ventas/Licitaciones")).toEqual({ section: "licitaciones", id: null });
  });

  it("reads an old address as the page it redirects to, before the redirect lands", () => {
    expect(parseShellHash(`#/crm/personas/${id}`)).toEqual({ section: "personas", id });
    expect(parseShellHash("#/ventas")).toEqual({ section: "oportunidades", id: null });
  });
});
