/**
 * «Próximo contacto»: when a case is to be contacted next. One rule (owner QA 2026-11-19), read by
 * Seguimiento (sheet, cards, Agenda), Hoy, the Pipeline and the case panel, so one case never shows
 * «hoy» in one place and «vie 20 nov» in another:
 *
 * 1. the earliest pending «Seguimiento …» reminder (one our later email or answered call already met
 *    is done, `pendingFollowUpTasks`);
 * 2. else the earliest other open task;
 * 3. else, while the case is paused (ahead or come back), the day the pause ends;
 * 4. else none.
 *
 * A reminder always wins over the pause: a case paused until Monday that someone called and set
 * for Friday is due Friday, not «3 d atrasado · Vuelve de pausa».
 */
import type { OpenTask, OpportunityCardData } from "./crmTypes";
import { isPaused, pauseDayIso } from "./stage";
import { isFollowUpTask, pendingFollowUpTasks } from "./today";

export type NextContactSource = "followup" | "task" | "pause";

export interface NextContact {
  /** ISO instant (a pause day is its local noon, `pauseDayIso`). */
  at: string;
  source: NextContactSource;
  /** The task behind it; null for a pause. */
  task: OpenTask | null;
  /** What to show next to the date: the task's title, or «Vuelve de pausa · motivo». */
  label: string;
}

const byDue = (a: OpenTask, b: OpenTask) => Date.parse(a.due_at) - Date.parse(b.due_at);

export function nextContact(card: OpportunityCardData, now: Date = new Date()): NextContact | null {
  const followUp = pendingFollowUpTasks(card).filter((t) => !!t.due_at)[0];
  if (followUp) return { at: followUp.due_at, source: "followup", task: followUp, label: followUp.title };
  const other = (card.open_tasks ?? []).filter((t) => !!t.due_at && !isFollowUpTask(t)).sort(byDue)[0];
  if (other) return { at: other.due_at, source: "task", task: other, label: other.title };
  if (card.paused_until && isPaused(card, now)) {
    return { at: pauseDayIso(card.paused_until), source: "pause", task: null, label: `Vuelve de pausa · ${card.pause_reason ?? "Sin motivo"}` };
  }
  return null;
}
