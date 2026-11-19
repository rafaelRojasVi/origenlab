/**
 * «Deshacer» for a case that just left the follow-up: a pause, or a close («Ya no sigue», «Perdida»).
 * One helper for every place that does it — Seguimiento's «⋯», its case panel, Hoy's pane — so the
 * toast, its wording and the undo command are the same everywhere (QA 2026-11-19).
 *
 * - A pause is undone by `resumeCase` against the version the pause left.
 * - A close is undone by `moveCase` from the closed stage back to the stage the case had before,
 *   against the version of the last receipt (a close is one or two `advance-case-stage` steps).
 */
import { caseRefusalText, moveCase, resumeCase, type CaseCommandReceipt } from "./caseCommands";
import type { OpportunityCardData } from "./crmTypes";
import { toast } from "./ui";

export type UndoableAction = "pausa" | "perdida";

/** The newest version a set of receipts left the case at, or null. */
export function lastVersion(receipts: CaseCommandReceipt[] | undefined, id: string): number | null {
  const mine = (receipts ?? []).filter((r) => r.opportunity_id === id && typeof r.opportunity_version === "number");
  return mine.length ? Math.max(...mine.map((r) => r.opportunity_version)) : null;
}

/** The stage the last receipt that names one left the case at (the closed stage), or null. */
export function closedStageOf(receipts: CaseCommandReceipt[] | undefined): string | null {
  return [...(receipts ?? [])].reverse().find((r) => typeof r.stage === "string")?.stage ?? null;
}

export async function undoClose(id: string, previousStage: string, closed: { stage: string; version: number }, after: () => void, from = "Seguimiento"): Promise<void> {
  try {
    await moveCase({ opportunity_id: id, stage: closed.stage, version: closed.version }, previousStage, `Deshecho desde ${from}: el caso sigue.`, null);
    toast("Listo: el caso vuelve al seguimiento.");
  } catch (err) {
    toast(caseRefusalText(err), "bad");
  }
  after();
}

export async function undoPause(id: string, version: number, after: () => void, from = "Seguimiento"): Promise<void> {
  try {
    await resumeCase({ opportunity_id: id, version }, `Pausa deshecha desde ${from}.`);
    toast("Listo: la pausa se deshizo.");
  } catch (err) {
    toast(caseRefusalText(err), "bad");
  }
  after();
}

/**
 * The «Deshacer» for `action` on `card` after its commands left `receipts`, or null when nothing
 * that can be undone was recorded (no version, or a close with no stage receipt).
 */
export function undoFor(
  action: UndoableAction,
  card: Pick<OpportunityCardData, "opportunity_id" | "stage">,
  receipts: CaseCommandReceipt[] | undefined,
  after: () => void,
  from = "Seguimiento",
): (() => void) | null {
  const id = card.opportunity_id;
  const version = lastVersion(receipts, id);
  if (version === null) return null;
  if (action === "pausa") return () => void undoPause(id, version, after, from);
  const closed = closedStageOf(receipts);
  if (!closed || closed === card.stage || !["lost", "abandoned"].includes(closed)) return null;
  return () => void undoClose(id, card.stage, { stage: closed, version }, after, from);
}

/** The toast after a pause or a close, with «Deshacer» when it can be undone. */
export function toastWithUndo(
  text: string,
  action: UndoableAction,
  card: Pick<OpportunityCardData, "opportunity_id" | "stage">,
  receipts: CaseCommandReceipt[] | undefined,
  after: () => void,
  tone: "good" | "warn" = "good",
  from = "Seguimiento",
): void {
  const undo = undoFor(action, card, receipts, after, from);
  toast(text, tone, undo ? { action: { label: "Deshacer", onClick: undo } } : {});
}
