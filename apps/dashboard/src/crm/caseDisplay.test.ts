import { describe, expect, it } from "vitest";
import { quoteProduct } from "./caseDisplay";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";

// Every value below is invented; the repository is public.
function withQuote(subject: string | null, filename: string | null): OpportunityCardData {
  const latest: RevisionCard = {
    revision_id: "r-1",
    revision_no: 1,
    status: "sent",
    origin: "historical_import",
    sent_at: "2026-09-30T12:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: filename ? { sha256: "a".repeat(64), filename } : null,
    gmail: { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.example.cl/m", subject },
    drive: null,
    quote_number: "01239-26",
  };
  return {
    opportunity_id: "00000000-0000-4000-8000-000000000001",
    title: "Caso",
    stage: "quoting",
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: null,
    quotes: [],
    quote_numbers: ["01239-26"],
    revision_count: 1,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
  };
}

describe("quoteProduct", () => {
  it("reads the product the quote email's subject names", () => {
    expect(quoteProduct(withQuote("Cotización Balanzas Ohaus", "CN01239-Ana Pérez.pdf"))).toBe("Balanzas Ohaus");
    expect(quoteProduct(withQuote("Re: Solicitud cotización porta y cubreobjeto", null))).toBe("Porta y cubreobjeto");
    expect(quoteProduct(withQuote("RV: URGENTE Cotización std 100 mOs", null))).toBe("Std 100 mOs");
    expect(quoteProduct(withQuote("Instituto Ficticio Solicitud de cotización de insumos", null))).toBe("Insumos");
    // «Campana» is a fume hood, not a «campaña».
    expect(quoteProduct(withQuote("Cotización Campana extractora", null))).toBe("Campana extractora");
  });

  it("adds the model the file name ends with when the subject leaves it out", () => {
    expect(quoteProduct(withQuote("Cotización Sonicador", "CN01239-Ana Pérez - Instituto-UP400St.pdf"))).toBe("Sonicador · UP400St");
    expect(quoteProduct(withQuote("Cotización Termobalanza MB32", "CN01239-Ana-MB32.pdf"))).toBe("Termobalanza MB32");
  });

  it("falls back to the model when the subject is a campaign, a form or says nothing", () => {
    expect(quoteProduct(withQuote("Fwd: Cotizacion", "CN01239-Ana Pérez - Instituto-MB120.pdf"))).toBe("MB120");
    expect(quoteProduct(withQuote("Re: Campaña de Invierno | Renueve su Ultrasonido", "CN01239-Ana-UP200St.pdf"))).toBe("UP200St");
    expect(quoteProduct(withQuote("Re: Inscripción Proveedor", "CN01239-Ana Pérez.pdf"))).toBeNull();
    expect(quoteProduct(withQuote(null, null))).toBeNull();
    // Campaign subjects a client replied to, seen in real cases.
    expect(quoteProduct(withQuote("Solicitud de cotización — Especial septiembre", "CN01220-Ana-UP100H.pdf"))).toBe("UP100H");
    expect(quoteProduct(withQuote("RE: EQUIPOS/INSUMOS PARA SU LABORATORIO - ORIGENLAB", "CN01168A-Ana - Instituto-T18.pdf"))).toBe("T18");
    expect(quoteProduct(withQuote("Suministros de equipos para el laboratorio", null))).toBeNull();
    expect(quoteProduct(withQuote("Re: Contacto Sonicador Ultrasonido Hielscher", "CN01187-Ana-UP400St.pdf"))).toBe("Sonicador Ultrasonido Hielscher · UP400St");
  });
});
