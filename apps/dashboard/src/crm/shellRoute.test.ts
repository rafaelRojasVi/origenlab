import { describe, expect, it } from "vitest";
import { CRM_NAV, crmHash, sectionTitle } from "./crmRoute";
import { LEGACY_REDIRECTS, parseShellHash, redirectHash } from "./shellRoute";

describe("shell route", () => {
  it("routes CRM hashes to the CRM, with a case id when it is a UUID", () => {
    const id = "6bafddfa-0000-4000-8000-000000000001";
    expect(parseShellHash(`#/crm/oportunidades/${id}`)).toEqual({ section: "oportunidades", id });
    expect(parseShellHash("#/crm")).toEqual({ section: "resumen", id: null });
    expect(redirectHash(`#/crm/oportunidades/${id}`)).toBeNull();
  });

  it("sends the bare root and unknown hashes to Resumen", () => {
    for (const hash of ["", "#", "#/", "#/nope", "#/crmx"]) {
      expect(parseShellHash(hash), hash).toEqual({ section: "resumen", id: null });
      expect(redirectHash(hash), hash).toBe("#/crm/resumen");
    }
  });

  it("redirects earlier-panel bookmarks to their CRM equivalent", () => {
    expect(redirectHash("#/cotizaciones")).toBe("#/crm/oportunidades");
    expect(redirectHash(`#/ventas?opportunity=sales_${"a".repeat(32)}`)).toBe("#/crm/oportunidades");
    expect(redirectHash("#/contactos")).toBe("#/crm/personas");
    expect(redirectHash("#/instituciones")).toBe("#/crm/organizaciones");
    expect(redirectHash("#/suppliers")).toBe("#/crm/proveedores");
    expect(redirectHash("#/archivo")).toBe("#/crm/drive");
    expect(redirectHash("#/revision")).toBe("#/crm/historial");
    expect(redirectHash("#/tenders")).toBe("#/crm/resumen");
    expect(redirectHash("#/catalogo")).toBe("#/crm/catalogo");
  });

  it("opens the catalog and one product by its id", () => {
    const id = "6bafddfa-0000-4000-8000-0000000000c1";
    expect(parseShellHash("#/crm/catalogo")).toEqual({ section: "catalogo", id: null });
    expect(parseShellHash(`#/crm/catalogo/${id}`)).toEqual({ section: "catalogo", id });
  });

  it("keeps the selected case from an earlier-panel bookmark", () => {
    const id = "6bafddfa-0000-4000-8000-000000000001";
    expect(redirectHash(`#/ventas?opportunity=${id}`)).toBe(`#/crm/oportunidades/${id}`);
    expect(redirectHash(`#/pipeline?opportunity=${id.toUpperCase()}`)).toBe(`#/crm/oportunidades/${id}`);
    expect(redirectHash(`#/casos?id=${id}`)).toBe(`#/crm/oportunidades/${id}`);
    expect(redirectHash(`#/cotizaciones?opportunity=${id}&tab=x`)).toBe(`#/crm/oportunidades/${id}`);
    // Not a case id, or not a section with a single-record view: the list, never a guess.
    expect(redirectHash("#/ventas?opportunity=../../x")).toBe("#/crm/oportunidades");
    expect(redirectHash(`#/contactos?id=${id}`)).toBe("#/crm/personas");
    expect(redirectHash(`#/tenders?id=${id}`)).toBe("#/crm/resumen");
  });

  it("covers every section of the earlier panel", () => {
    const old = [
      "today", "inbox", "pipeline", "deals", "prospectos", "cotizaciones", "catalogo", "suppliers", "tenders",
      "payments-logistics", "contacts", "contactos", "instituciones", "crm-v2", "revision", "casos", "importacion",
      "archivo", "system",
    ];
    for (const s of old) expect(LEGACY_REDIRECTS[s], s).toBeDefined();
  });
});

describe("Cotizador route", () => {
  const id = "6bafddfa-0000-4000-8000-0000000000d1";

  it("opens the module, and a quote in it by its id, round-tripping through crmHash", () => {
    expect(parseShellHash("#/crm/cotizador")).toEqual({ section: "cotizador", id: null });
    expect(parseShellHash(`#/crm/cotizador/${id}`)).toEqual({ section: "cotizador", id });
    expect(crmHash("cotizador", id)).toBe(`#/crm/cotizador/${id}`);
    expect(redirectHash(`#/crm/cotizador/${id}`)).toBeNull();
  });

  it("is in the navigation between Oportunidades and Organizaciones, titled «Cotizador», for sales and admin", () => {
    const ids = CRM_NAV.map((n) => n.id);
    expect(ids.indexOf("cotizador")).toBe(ids.indexOf("oportunidades") + 1);
    expect(ids.indexOf("organizaciones")).toBe(ids.indexOf("cotizador") + 1);
    expect(sectionTitle("cotizador")).toBe("Cotizador");
    expect(CRM_NAV.find((n) => n.id === "cotizador")?.quoting).toBe(true);
  });

  it("redirects the old one-quote address to the module's tab", () => {
    expect(redirectHash(`#/crm/cotizar/${id}`)).toBe(`#/crm/cotizador/${id}`);
    expect(redirectHash(`#/crm/cotizar/${id.toUpperCase()}`)).toBe(`#/crm/cotizador/${id}`);
    expect(redirectHash("#/crm/cotizar")).toBe("#/crm/cotizador");
    expect(redirectHash("#/crm/cotizar/nope")).toBe("#/crm/cotizador");
    // Read before the redirect lands, it already opens the module.
    expect(parseShellHash(`#/crm/cotizar/${id}`)).toEqual({ section: "cotizador", id });
  });

  it("drops an id that is not a UUID", () => {
    expect(parseShellHash("#/crm/cotizador/nope")).toEqual({ section: "cotizador", id: null });
  });
});
