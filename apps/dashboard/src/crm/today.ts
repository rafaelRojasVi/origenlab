/**
 * «Hoy»: what the open cases ask of an operator today, read from the pipeline cards. Pure — every
 * list here is computed in the browser from data the cards already carry, and nothing is stored.
 *
 * - **Te toca responder**: the client wrote after OrigenLab's last email on the case, and nobody has
 *   scheduled a task on the case since («No requiere respuesta», «En pausa»): a task set after the
 *   client's email is the operator's answer to it. A newer email from the client brings it back.
 * - **Seguimientos**: one list, coloured like a traffic light by the 3 · 14 · 30-day rhythm counted
 *   from OrigenLab's last touch (the quote, or a later email it sent): green from day 3 (first
 *   follow-up), yellow from day 14 (second), red from day 30 (close?). A case whose «Seguimiento …»
 *   task is due today carries that task — from day 3, so a quote sent today is not chased today —
 *   and the task counts as done once OrigenLab writes on the thread on or after its day (no
 *   «Hecho» needed). A case with any other open task is already planned and is left out, and so
 *   is an undecided case whose stage is only the historical import's trace («Decidir casos»).
 * - **Vencidas**: open tasks (not follow-ups, which carry their own red rhythm) at least 3 days
 *   overdue — one's own from day 3, anyone's from day 7 (owner decision 2026-10-10: a task
 *   nobody did for a week is everyone's), most overdue first. They leave «Otras tareas».
 * - **Otras tareas**: the open `crm.task` rows due by the end of today that are not follow-ups
 *   («Retomar: …», a call), overdue first.
 * - **Instituciones por confirmar**: the machine-proposed institutions of open cases.
 */
import { conversation, lastTouch } from "./caseDisplay";
import type { OpenTask, OpportunityCardData } from "./crmTypes";
import { pausedUntil, stageBasis } from "./stage";

const DAY_MS = 86_400_000;
const CLOSED = new Set(["won", "lost", "abandoned"]);

export function isOpenCase(card: OpportunityCardData): boolean {
  return !card.closed_at && !CLOSED.has(card.stage);
}

/** The end of `now`'s day in the browser's zone. */
export function endOfDay(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
}

export interface DueTask {
  card: OpportunityCardData;
  task: OpenTask;
  /** Whole days past due; 0 for today. */
  overdueDays: number;
}

/** «Seguimiento de 01239-26»: the task «Decidir casos» and «+1 semana» write for a follow-up. */
export function isFollowUpTask(task: OpenTask): boolean {
  return /^\s*seguimiento\b/i.test(task.title);
}

function dueToday(task: OpenTask, now: Date): number | null {
  const due = Date.parse(task.due_at);
  if (Number.isNaN(due) || due > endOfDay(now).getTime()) return null;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.max(0, Math.ceil((startOfToday - due) / DAY_MS));
}

/** Open tasks due by tonight that are not follow-ups — those live in «Seguimientos». */
export function tasksDue(cards: OpportunityCardData[], now: Date): DueTask[] {
  const out: DueTask[] = [];
  for (const card of cards) {
    if (!isOpenCase(card)) continue;
    for (const task of card.open_tasks ?? []) {
      if (isFollowUpTask(task)) continue;
      const overdueDays = dueToday(task, now);
      if (overdueDays !== null) out.push({ card, task, overdueDays });
    }
  }
  return out.sort((a, b) => a.task.due_at.localeCompare(b.task.due_at));
}

/** Days overdue from which a task is «Vencida» for its owner, and from which it is for everyone. */
export const OVERDUE_OWN_DAYS = 3;
export const OVERDUE_ALL_DAYS = 7;

export interface OverdueTask extends DueTask {
  /** The task belongs to someone else and reached the week: shown to every profile. */
  escalated: boolean;
}

/**
 * Open non-follow-up tasks overdue by `OVERDUE_OWN_DAYS` or more that are `me`'s, plus anyone's
 * overdue by `OVERDUE_ALL_DAYS` or more. `me` is the profile's display name as the API labels
 * task owners; without one (an older session) only the escalated ones show. Most overdue first.
 */
export function overdueTasks(cards: OpportunityCardData[], now: Date, me: string | null): OverdueTask[] {
  const out: OverdueTask[] = [];
  for (const due of tasksDue(cards, now)) {
    if (due.overdueDays < OVERDUE_OWN_DAYS) continue;
    const mine = me !== null && due.task.owner === me;
    const escalated = due.overdueDays >= OVERDUE_ALL_DAYS;
    if (!mine && !escalated) continue;
    out.push({ ...due, escalated: escalated && !mine });
  }
  return out.sort((a, b) => b.overdueDays - a.overdueDays || a.task.due_at.localeCompare(b.task.due_at));
}

export interface Reply {
  card: OpportunityCardData;
  at: string;
  url: string | null;
}

export function repliesToAnswer(cards: OpportunityCardData[], now: Date): Reply[] {
  const out: Reply[] = [];
  for (const card of cards) {
    if (!isOpenCase(card) && !wroteAfterWinning(card)) continue;
    const conv = conversation(card, now);
    if (conv.kind !== "replied" || !conv.at) continue;
    // On a won case a task is refused, so «Atendido» leaves a note instead; on an open case only a
    // task answers («No requiere respuesta»): an internal note must never hide a client's email.
    if (answeredByTask(card, conv.at, now) || (wroteAfterWinning(card) && notedAfter(card, conv.at))) continue;
    out.push({ card, at: conv.at, url: conv.url });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}

/**
 * A won case is not over for the client: the payment, the delivery date, the invoice all arrive
 * on its thread. An email sent after the case was marked won is a reply to answer; one sent before
 * was already read when the case was won. A lost case's client writing back is a new request.
 */
export function wroteAfterWinning(card: OpportunityCardData): boolean {
  const inbound = card.last_contact?.inbound?.at ?? null;
  return card.stage === "won" && !!card.closed_at && !!inbound && Date.parse(inbound) > Date.parse(card.closed_at);
}

/**
 * The operator wrote a note on the case after the client's email at `at`: «Atendido» on a won
 * case, where a task is refused. The note is the answer the list needs; the next email from the
 * client puts the case back. `repliesToAnswer` consults it for won cases only.
 */
export function notedAfter(card: OpportunityCardData, at: string): boolean {
  const note = card.last_note?.created_at ?? null;
  return !!note && Date.parse(note) > Date.parse(at);
}

/**
 * The operator already decided what the client's email at `at` needs («gracias, le aviso» needs
 * nothing): a task on the case was scheduled after it. An older API sends no `created_at`; then a
 * case «En pausa» counts as decided.
 */
function answeredByTask(card: OpportunityCardData, at: string, now: Date): boolean {
  const open = card.open_tasks ?? [];
  if (open.some((t) => t.created_at && Date.parse(t.created_at) > Date.parse(at))) return true;
  return open.every((t) => !t.created_at) && pausedUntil(card, now) !== null;
}

export type Rhythm = "primero" | "segundo" | "cerrar";

/** The traffic light: green, yellow, red. */
export const RHYTHM: { key: Rhythm; label: string; from: number; hint: string; tone: "good" | "warn" | "bad" }[] = [
  { key: "primero", label: "Primer seguimiento", from: 3, hint: "hasta 13 días sin respuesta", tone: "good" },
  { key: "segundo", label: "Segundo seguimiento", from: 14, hint: "14 a 29 días sin respuesta", tone: "warn" },
  { key: "cerrar", label: "¿Cerrar?", from: 30, hint: "30 días o más: casi nunca vuelve", tone: "bad" },
];

export interface FollowUp {
  card: OpportunityCardData;
  /** Days since OrigenLab's last touch: the quote, or a later email it sent. */
  days: number;
  rhythm: Rhythm;
  /** The last touch was a follow-up email, not the quote itself. */
  byEmail: boolean;
  /** The «Seguimiento …» task due today that put the case here, if one did. */
  task: DueTask | null;
}

export function followUpsDue(cards: OpportunityCardData[], now: Date): FollowUp[] {
  const out: FollowUp[] = [];
  for (const card of cards) {
    if (!isOpenCase(card)) continue;
    const open = card.open_tasks ?? [];
    // OrigenLab's last touch after the quote: an email on the thread, or a logged follow-up
    // («Registrar seguimiento», a call). One clock with the board's status line (`lastTouch`).
    const touch = lastTouch(card);
    const email = touch.by === "us" ? touch.at : null;
    // A follow-up task is done once OrigenLab writes on the case on or after its day: nobody has
    // to press «Hecho» — the email the sync captures, or the note, is the proof.
    const written = (t: OpenTask) => email !== null && Date.parse(email) >= startOfLocalDay(Date.parse(t.due_at));
    const pending = open.filter((t) => !(isFollowUpTask(t) && written(t)));
    // A case «Decidir casos» scheduled is decided, even if its stage still reads historical.
    const decided = open.some(isFollowUpTask);
    let task: DueTask | null = null;
    for (const t of [...pending].sort((a, b) => a.due_at.localeCompare(b.due_at))) {
      const overdueDays = isFollowUpTask(t) ? dueToday(t, now) : null;
      if (overdueDays !== null) {
        task = { card, task: t, overdueDays };
        break;
      }
    }
    if (!task && (pending.length > 0 || (!decided && stageBasis(card, now) === "historical_import"))) continue;
    const sent = card.latest_revision?.sent_at ?? null;
    if (!sent && !task) continue;
    if (!task && conversation(card, now).kind === "replied") continue;
    const byEmail = email !== null;
    const touchAt = byEmail ? (email as string) : sent;
    const days = touchAt ? Math.max(0, Math.floor((now.getTime() - Date.parse(touchAt)) / DAY_MS)) : 0;
    // Day 3 first, task or not: a quote that went out today is not chased today.
    if (days < RHYTHM[0].from) continue;
    const rhythm = days >= RHYTHM[2].from ? "cerrar" : days >= RHYTHM[1].from ? "segundo" : "primero";
    out.push({ card, days, rhythm, byEmail, task });
  }
  return out.sort((a, b) => b.days - a.days);
}

function startOfLocalDay(t: number): number {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export interface OrgToConfirm {
  organization_id: string;
  name: string;
  version: number | null;
  cases: OpportunityCardData[];
}

export function organizationsToConfirm(cards: OpportunityCardData[]): OrgToConfirm[] {
  const byId = new Map<string, OrgToConfirm>();
  for (const card of cards) {
    const org = card.organization;
    if (!isOpenCase(card) || !org || org.confirmation !== "machine_proposed") continue;
    const entry = byId.get(org.organization_id) ?? {
      organization_id: org.organization_id,
      name: org.name ?? "Sin nombre",
      version: typeof org.version === "number" ? org.version : null,
      cases: [],
    };
    entry.cases.push(card);
    byId.set(org.organization_id, entry);
  }
  return [...byId.values()].sort((a, b) => b.cases.length - a.cases.length || a.name.localeCompare(b.name, "es"));
}

export function historicalCount(cards: OpportunityCardData[], now: Date = new Date()): number {
  return cards.filter((c) => stageBasis(c, now) === "historical_import").length;
}

/** The two counts «Hoy»'s header shows: cases to decide and follow-ups due today. */
export function todayCounts(cards: OpportunityCardData[], now: Date = new Date()): { replies: number; decide: number; followUps: number } {
  return { replies: repliesToAnswer(cards, now).length, decide: historicalCount(cards, now), followUps: followUpsDue(cards, now).length };
}
