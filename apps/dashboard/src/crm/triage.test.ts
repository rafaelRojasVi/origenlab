import { describe, expect, it } from "vitest";
import { approvalMove, sortInbox, stageLabel, TRIAGE_PATHS, type TriageReading } from "./triage";
import { MAIL_RULES_PATHS } from "./mailRules";
import { parseProducts } from "./pages/TriagePanel";

const CASE = { opportunity_id: "o-1", title: "Balanzas", stage: "quoting", version: 4 };

describe("approvalMove — what «Aprobar» does to the case", () => {
  it("moves the one case of the thread to the suggested stage when the board can walk there", () => {
    expect(approvalMove({ stage: "negotiating", cases: [CASE] })).toEqual({ case: CASE, to: "negotiating" });
    expect(approvalMove({ stage: "lost", cases: [CASE] })).toEqual({ case: CASE, to: "lost" });
  });

  it("moves nothing when the stage is the same, not a case, unclear, «Ganada», or the thread is on 0 or 2 cases", () => {
    expect(approvalMove({ stage: "quoting", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "not_a_case", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "unclear", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "won", cases: [CASE] })).toBeNull(); // needs «Marcar ganada» with its revision
    expect(approvalMove({ stage: "negotiating", cases: [] })).toBeNull();
    expect(approvalMove({ stage: "negotiating", cases: [CASE, { ...CASE, opportunity_id: "o-2" }] })).toBeNull();
    expect(approvalMove({ stage: "lead", cases: [{ ...CASE, stage: "won" }] })).toBeNull(); // a closed case
  });
});

describe("the triage vocabulary in the board's words", () => {
  it("labels stages as the Tablero does, plus the two non-stage answers", () => {
    expect(stageLabel("negotiating")).toBe("Conversación");
    expect(stageLabel("not_a_case")).toBe("No es un caso");
    expect(stageLabel("unclear")).toBe("No se sabe");
    expect(stageLabel(null)).toBe("—");
  });

  it("reads the allow-listed queue; the verdict goes through the mail-rules client", () => {
    expect(TRIAGE_PATHS).toEqual({ readings: "/v2/workspace/triage-readings" });
    expect(MAIL_RULES_PATHS.reviewTriage).toBe("/v2/commands/review-triage");
  });
});

describe("parseProducts — the correction's product lines", () => {
  it("reads «2 × balanza», «3x pipeta» and plain lines, skipping blanks", () => {
    expect(parseProducts("2 × balanza analítica\n\n3x pipeta pasteur\nagitador")).toEqual([
      { description: "balanza analítica", model: null, quantity: 2, catalog_product_id: null },
      { description: "pipeta pasteur", model: null, quantity: 3, catalog_product_id: null },
      { description: "agitador", model: null, quantity: null, catalog_product_id: null },
    ]);
  });
});

describe("sortInbox — which emails «Hoy» asks about", () => {
  const base = {
    source_record_id: "s", version: "v", class: "quote_request", reasons: [], model_state: "off", stage: null,
    intent: null, urgency: null, summary_es: null, needs_reply: null, requester_organization: null, products: [],
    candidates: [], cases: [], transitions: [], review: null, sender_is_supplier: false,
  };
  const r = (id: string, over: Partial<TriageReading>): TriageReading =>
    ({ ...base, assertion_id: id, subject: id, sender: `${id}@cliente.example`, sent_at: "2026-10-09T10:00:00Z",
       thread_id: `t-${id}`, ...over }) as TriageReading;

  it("asks only about new emails from people, one per thread, newest first", () => {
    const { ask, hidden } = sortInbox([
      r("nuevo", { thread_id: "t1" }),
      r("mismo-hilo", { thread_id: "t1", sent_at: "2026-10-08T10:00:00Z" }),
      r("en-caso", { cases: [CASE] }),
      r("proveedor", { sender_is_supplier: true }),
      r("oferta", { intent: "supplier_offer" }),
      r("aviso", { sender: "no-reply@portal.example" }),
      r("info", { sender: "info@publicidad.example" }),
      r("reenvio", { sender: "contacto@labdelivery.cl" }),
      r("masivo", { class: "bulk" }),
    ]);
    expect(ask.map((x) => x.assertion_id)).toEqual(["nuevo"]);
    expect(hidden).toEqual({ "en un caso": 1, proveedor: 2, "aviso automático": 3, "reenvío antiguo": 1, "mismo hilo": 1 });
  });
});
