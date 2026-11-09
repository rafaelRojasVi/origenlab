/**
 * How a phone call ended (`crm.note.call_outcome`), said once for the dashboard. Mirrors
 * `apps/api/src/origenlab_api/v2/call_outcome.py` (a test reads that file, so the two cannot
 * drift). A «Llamada» is an ordinary note with `note_kind: "llamada"`: the outcome and the agreed
 * next step are fields of their own, never text folded into the body.
 */

export const CALL_OUTCOMES = ["contesto", "no_contesto", "buzon", "numero_malo", "reunion"] as const;

export type CallOutcome = (typeof CALL_OUTCOMES)[number];

export const CALL_OUTCOME_LABEL: Record<CallOutcome, string> = {
  contesto: "Contestó",
  no_contesto: "No contestó",
  buzon: "Buzón de voz",
  numero_malo: "Número equivocado",
  reunion: "Reunión agendada",
};

/** `note_call_next_step_check`: at most this many characters. */
export const CALL_NEXT_STEP_MAX = 500;

/** What an operator reads for an outcome; never the raw key. */
export function callOutcomeLabel(outcome: string): string {
  return (CALL_OUTCOME_LABEL as Record<string, string>)[outcome] ?? "Otro resultado";
}

/** A note needs a body: when the operator wrote none, the call says what happened. */
export function defaultCallBody(outcome: CallOutcome): string {
  return `Llamada: ${CALL_OUTCOME_LABEL[outcome]}`;
}

export interface CallNoteBody {
  subject_kind: "opportunity";
  subject_id: string;
  body: string;
  note_kind: "llamada";
  call_outcome: CallOutcome;
  call_next_step?: string;
}

/** The `add-note` body for one call: trimmed, a blank next step left out, a default body. */
export function buildCallNote(
  opportunityId: string,
  call: { outcome: CallOutcome; nextStep: string; text: string },
): CallNoteBody {
  const nextStep = call.nextStep.trim();
  return {
    subject_kind: "opportunity",
    subject_id: opportunityId,
    body: call.text.trim() || defaultCallBody(call.outcome),
    note_kind: "llamada",
    call_outcome: call.outcome,
    ...(nextStep ? { call_next_step: nextStep } : {}),
  };
}
