import { describe, expect, it } from "vitest";
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
