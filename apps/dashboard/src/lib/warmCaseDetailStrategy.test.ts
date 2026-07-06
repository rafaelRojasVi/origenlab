import { describe, expect, it } from "vitest";
import type { WarmCaseItem } from "../api/commercialTypes";
import {
  buildWarmCaseDetailView,
  formatWarmCaseNextAction,
  parseAllowedHttpUrl,
  sanitizeOperatorPreview,
} from "./warmCaseDetailStrategy";

function row(overrides: Partial<WarmCaseItem>): WarmCaseItem {
  return {
    case_id: "1",
    last_email_id: 1,
    last_seen_at: null,
    account_name: "IKA",
    contact_email: "beatriz.bonon@ika.net.br",
    subject: "RE: RV10.70 price",
    category: "supplier_quote_received",
    status: "waiting",
    next_action: "supplier_reply",
    equipment_signal: "rotary evaporator",
    snippet: "Price list attached",
    gmail_url: "https://mail.google.com/mail/u/0/#inbox/secret",
    ...overrides,
  };
}

describe("warmCaseDetailStrategy (español)", () => {
  it("estrategia de cotización de proveedor", () => {
    const detail = buildWarmCaseDetailView(row({ category: "supplier_quote_received" }));
    expect(detail.recommendedStrategy).toMatch(/oportunidad de cliente/i);
    expect(detail.linkedSection).toBe("suppliers");
    expect(detail.categoryLabel).toBe("Cotización de proveedor recibida");
  });

  it("estrategia de pago administrativo", () => {
    const detail = buildWarmCaseDetailView(
      row({
        category: "payment_admin",
        contact_email: "pay@bancochile.cl",
        subject: "FACTURA 6",
      }),
    );
    expect(detail.recommendedStrategy).toMatch(/no uses este hilo para cotizar/i);
    expect(detail.linkedSection).toBe("payments-logistics");
  });

  it("estrategia de evidencia de negocio", () => {
    const detail = buildWarmCaseDetailView(
      row({
        category: "deal_evidence_candidate",
        subject: "Remite OC",
        account_name: "CEAF",
      }),
    );
    expect(detail.recommendedStrategy).toMatch(/Negocios/i);
    expect(detail.linkedSection).toBe("deals");
  });

  it("sanitizeOperatorPreview oculta datos sensibles", () => {
    const text = sanitizeOperatorPreview(
      "See https://mail.google.com/x and cuenta 12345678901234",
    );
    expect(text).not.toMatch(/https?:\/\//);
    expect(text).toContain("[oculto]");
  });

  it("parseAllowedHttpUrl allows https and rejects dangerous schemes", () => {
    expect(parseAllowedHttpUrl("https://example.com/path")?.href).toBe("https://example.com/path");
    expect(parseAllowedHttpUrl("http://example.com")).not.toBeNull();
    expect(parseAllowedHttpUrl("javascript:alert(1)")).toBeNull();
    expect(parseAllowedHttpUrl("data:text/html,hi")).toBeNull();
    expect(parseAllowedHttpUrl("blob:https://example.com/uuid")).toBeNull();
    expect(parseAllowedHttpUrl("file:///etc/passwd")).toBeNull();
    expect(parseAllowedHttpUrl("//evil.example")).toBeNull();
    expect(parseAllowedHttpUrl(" https://evil.com")).toBeNull();
    expect(parseAllowedHttpUrl("https://evil.com\r\nSet-Cookie: x")).toBeNull();
  });

  it("sanitizeOperatorPreview redacts javascript and data URLs via parser", () => {
    const text = sanitizeOperatorPreview("click javascript:alert(1) or data:text/html,x");
    expect(text).not.toContain("javascript:");
    expect(text).not.toContain("data:text");
    expect(text).toContain("[oculto]");
  });

  it("aplica resumen seguro para caso RG Energía RV10.70", () => {
    const detail = buildWarmCaseDetailView(
      row({
        category: "client_opportunity",
        account_name: "RG ENERGIA SPA",
        contact_email: "contacto@labdelivery.cl",
        subject: "RV: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
      }),
    );
    expect(detail.inferredSummary).toContain("RV10.70");
    expect(detail.inferredSummary).toContain("Falta confirmar moneda y despacho");
    expect(detail.recommendedStrategy).toContain("San Bernardo");
  });

  it("uses category fallback when next_action is unmapped", () => {
    const detail = buildWarmCaseDetailView(
      row({
        category: "client_opportunity",
        next_action: "unknown_token",
      }),
    );
    expect(detail.nextActionLabel).not.toBe("Sin clasificar");
    expect(detail.nextActionLabel).toMatch(/Validar equipo/i);
  });

  it("preserves human-readable next_action sentences", () => {
    const sentence = "Cotización enviada; monitorear respuesta del cliente.";
    expect(
      formatWarmCaseNextAction(
        row({
          category: "quote_sent",
          next_action: sentence,
        }),
      ),
    ).toBe(sentence);
    const detail = buildWarmCaseDetailView(
      row({
        category: "quote_sent",
        next_action: sentence,
      }),
    );
    expect(detail.nextActionLabel).toBe(sentence);
  });

  it("preserves mapped legacy next_action tokens", () => {
    expect(formatWarmCaseNextAction(row({ next_action: "follow" }))).toBe("Dar seguimiento");
    expect(formatWarmCaseNextAction(row({ next_action: "supplier_reply" }))).toBe(
      "Revisar propuesta del proveedor",
    );
  });

  it("aplica resumen seguro para cotización CRTOP", () => {
    const detail = buildWarmCaseDetailView(
      row({
        category: "supplier_quote_received",
        account_name: "CRTOP",
        contact_email: "ariel@crtopmachine.com",
        subject: "Re: Thank you very much for your inquiry about our reactor.",
      }),
    );
    expect(detail.inferredSummary).toContain("US$10,600 EXW");
    expect(detail.recommendedStrategy).toContain("HS code");
  });
});
