/**
 * «Próximo contacto»: when a case is to be contacted next. One rule (owner QA 2026-11-19), read by
 * Seguimiento (sheet, cards, Agenda), Hoy, the Pipeline and the case panel, so one case never shows
 * «hoy» in one place and «vie 20 nov» in another:
 *
 * 1. the earliest pending «Seguimiento …» reminder (one our later email or answered call already met
 *    is done, `pendingFollowUpTasks`);
 * 2. else the earliest other open task;
 * 3. else, while the case is paused (ahead or come back), the day the pause ends;
 * 4. else the follow-up rhythm's day (`rhythmNext`): today when Hoy lists it as «Seguimiento N de 3»,
 *    else the next step's day — the same day Hoy's agenda and the Pipeline show (QA 2026-11-22:
 *    Seguimiento read «Sin fecha» for 45 cases Hoy had dated);
 * 5. else none.
 *
 * A reminder always wins over the pause: a case paused until Monday that someone called and set
 * for Friday is due Friday, not «3 d atrasado · Vuelve de pausa». But while the pause is still
 * ahead, an overdue task (left from before the pause) does not count (`livePauseFilter`).
 *
 * A pause whose day has come is not a future date (QA 2026-11-19): when the follow-up rhythm has the
 * case due today (Hoy lists it as «Seguimiento N de 3»), that is the próximo contacto
 * (`source: "rhythm"`); otherwise the pause stays, worded in the past («Volvió de pausa»).
 */
import type { OpenTask, OpportunityCardData } from "./crmTypes";
import { isMaskedPhone } from "./redaction";
import { isPaused, pauseDayIso, pausedUntil } from "./stage";
import { followUpStepLabel, isFollowUpTask, livePauseFilter, pendingFollowUpTasks, rhythmNext } from "./today";

export type NextContactSource = "followup" | "task" | "pause" | "rhythm";

export interface NextContact {
  /** ISO instant (a pause day is its local noon, `pauseDayIso`). */
  at: string;
  source: NextContactSource;
  /** The task behind it; null for a pause. */
  task: OpenTask | null;
  /** What to show next to the date: the task's title, or «Vuelve de pausa · motivo». */
  label: string;
  /** For `source: "rhythm"`, the rhythm step: the follow-ups already sent plus one (4 = the closing email). */
  step?: 1 | 2 | 3 | 4;
}

const byDue = (a: OpenTask, b: OpenTask) => Date.parse(a.due_at) - Date.parse(b.due_at);

export function nextContact(card: OpportunityCardData, now: Date = new Date()): NextContact | null {
  // While a pause is still ahead, an overdue task is a leftover from before the pause (owner
  // 2026-11-19: cases paused until December showed «Seguimiento · hoy» from an October reminder).
  // A task due today or later — someone called and set Friday — still wins.
  const live = livePauseFilter(card, now);
  const followUp = pendingFollowUpTasks(card).filter((t) => !!t.due_at && live(t))[0];
  if (followUp) return { at: followUp.due_at, source: "followup", task: followUp, label: followUp.title };
  const other = (card.open_tasks ?? []).filter((t) => !!t.due_at && !isFollowUpTask(t) && live(t)).sort(byDue)[0];
  if (other) return { at: other.due_at, source: "task", task: other, label: other.title };
  if (card.paused_until && isPaused(card, now)) {
    const reason = card.pause_reason ?? "Sin motivo";
    const at = pauseDayIso(card.paused_until);
    if (pausedUntil(card, now) !== null) return { at, source: "pause", task: null, label: `Vuelve de pausa · ${reason}` };
    // The pause came back: the rhythm's follow-up due today wins, as in Hoy.
    const due = rhythmNext(card, now);
    if (due?.dueToday) return rhythmContact(due);
    const today = card.paused_until.slice(0, 10) === localIsoDay(now);
    return { at, source: "pause", task: null, label: `${today ? "Vuelve" : "Volvió"} de pausa · ${reason}` };
  }
  const rhythm = rhythmNext(card, now);
  return rhythm ? rhythmContact(rhythm) : null;
}

/** The rhythm's day at the morning hour a reminder defaults to: not a made-up time that moves with each read. */
function rhythmContact(r: { day: Date; step: 1 | 2 | 3 | 4; close: boolean }): NextContact {
  const morning = new Date(r.day.getFullYear(), r.day.getMonth(), r.day.getDate(), 9, 0);
  return { at: morning.toISOString(), source: "rhythm", task: null, label: followUpStepLabel(r.step, r.close), step: r.step };
}

function localIsoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * The channel of the próximo contacto: the one chosen in Seguimiento (`follow_up.channel`), else
 * the one Seguimiento shows by default — a call when the case has a phone we can read, else an
 * email. Seguimiento's sheet and Hoy's queue both read it, so one case never says «Llamada» in one
 * place and «correo» in the other (QA 2026-11-19).
 */
export function nextContactChannel(card: OpportunityCardData): "llamada" | "correo" | "whatsapp" {
  const chosen = card.follow_up?.channel ?? null;
  if (chosen) return chosen;
  const phone = card.contact?.phone || null;
  return phone && !isMaskedPhone(phone) ? "llamada" : "correo";
}
