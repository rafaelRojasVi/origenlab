import { describe, expect, it } from "vitest";
import { approvalMove, stageLabel, TRIAGE_PATHS } from "./triage";
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
