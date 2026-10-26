import { describe, expect, it } from "vitest";
import { RULE_GUIDE, actionSentence, type OpsAction } from "./operations";

const base: OpsAction = {
  receipt_id: "r-1", at: "2026-10-13T12:00:00+00:00", rule_id: "R1", automatic: true, today: true,
  case_id: "c-1", case_title: "Caso X", quote_number: null, undone: false,
};

describe("rule guide", () => {
  it("explains exactly R1–R17, in full", () => {
    expect(Object.keys(RULE_GUIDE)).toEqual(["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15", "R16", "R17"]);
    for (const [id, g] of Object.entries(RULE_GUIDE)) {
      for (const field of [g.cuando, g.hace, g.ejemplo]) expect(field.trim().length, id).toBeGreaterThan(10);
    }
  });

  it("marks as automatic only the rules the API runs on its timer", () => {
    const auto = Object.entries(RULE_GUIDE).filter(([, g]) => g.automatica).map(([id]) => id);
    expect(auto).toEqual(["R1", "R2", "R3", "R4", "R7", "R9", "R11", "R12", "R13", "R14", "R15", "R16", "R17"]);
  });
});

describe("actionSentence", () => {
  it("says what the system did, to which case and why", () => {
    expect(actionSentence(base)).toBe("Vinculó un correo al caso «Caso X» — mismo hilo de Gmail (R1)");
  });
  it("names the quote when a case was opened with one", () => {
    expect(actionSentence({ ...base, rule_id: "R11", case_title: "Solicitud — Gradillas" }))
      .toBe("Atendió una solicitud reenviada: el caso «Solicitud — Gradillas» — solicitud reenviada a contacto@ (R11)");
    expect(actionSentence({ ...base, rule_id: "R10", case_title: "Instituto Ficticio — cotización 09876-26", quote_number: "09876-26" }))
      .toBe("Abrió el caso «Instituto Ficticio — cotización 09876-26» con la cotización 09876-26 — institución del PDF «por confirmar» (R10)");
  });
  it("says R14 recorded a quote on its case and R15 confirmed an institution", () => {
    expect(actionSentence({ ...base, rule_id: "R14", quote_number: "09876-26" }))
      .toBe("Registró la cotización 09876-26 en el caso «Caso X» — el correo ya estaba en el caso (R14)");
    expect(actionSentence({ ...base, rule_id: "R15", case_id: null, case_title: null, organization_name: "Acme Ejemplo SpA" }))
      .toBe("Confirmó la institución «Acme Ejemplo SpA» — cotización enviada hace 3 días o más (R15)");
    expect(actionSentence({ ...base, rule_id: "R16", quote_number: "09876A-26" }))
      .toBe("Marcó enviada la cotización 09876A-26 en el caso «Caso X» — se envió el PDF del Cotizador (R16)");
    expect(actionSentence({ ...base, rule_id: "R17", quote_number: "09876-26" }))
      .toBe("Pasó a «Conversación» el caso «Caso X» — el cliente respondió a la cotización (R17)");
  });
  it("says when it was undone", () => {
    expect(actionSentence({ ...base, undone: true })).toMatch(/· deshecho$/);
  });
});
