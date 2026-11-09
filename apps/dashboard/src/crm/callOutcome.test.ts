import { describe, expect, it } from "vitest";
import {
  CALL_NEXT_STEP_MAX,
  CALL_OUTCOMES,
  CALL_OUTCOME_LABEL,
  buildCallNote,
  callOutcomeLabel,
  defaultCallBody,
} from "./callOutcome";

describe("call outcomes", () => {
  it("names the five outcomes, in the order the API lists them (a test in apps/api checks the two files agree)", () => {
    expect(CALL_OUTCOMES).toEqual(["contesto", "no_contesto", "buzon", "numero_malo", "reunion"]);
    expect(Object.keys(CALL_OUTCOME_LABEL)).toEqual([...CALL_OUTCOMES]);
    expect(CALL_NEXT_STEP_MAX).toBe(500);
  });

  it("labels every outcome in Spanish and never shows the raw key", () => {
    expect(CALL_OUTCOME_LABEL).toEqual({
      contesto: "Contestó",
      no_contesto: "No contestó",
      buzon: "Buzón de voz",
      numero_malo: "Número equivocado",
      reunion: "Reunión agendada",
    });
    for (const key of CALL_OUTCOMES) expect(callOutcomeLabel(key)).not.toBe(key);
    expect(callOutcomeLabel("telepatia")).toBe("Otro resultado");
  });

  it("writes a default body when the operator wrote nothing", () => {
    expect(defaultCallBody("contesto")).toBe("Llamada: Contestó");
    expect(defaultCallBody("buzon")).toBe("Llamada: Buzón de voz");
  });
});

describe("buildCallNote", () => {
  const base = { subject_kind: "opportunity" as const, subject_id: "caso-ficticio" };

  it("sends the kind, the outcome, the trimmed next step and the default body", () => {
    expect(buildCallNote("caso-ficticio", { outcome: "contesto", nextStep: "  Mandar la ficha técnica  ", text: "   " })).toEqual({
      ...base,
      body: "Llamada: Contestó",
      note_kind: "llamada",
      call_outcome: "contesto",
      call_next_step: "Mandar la ficha técnica",
    });
  });

  it("leaves a blank next step out and uses the operator's text as the body when there is some", () => {
    const body = buildCallNote("caso-ficticio", { outcome: "no_contesto", nextStep: "  \n ", text: "  Sonó ocupado.  " });
    expect(body).toEqual({ ...base, body: "Sonó ocupado.", note_kind: "llamada", call_outcome: "no_contesto" });
    expect("call_next_step" in body).toBe(false);
  });
});
