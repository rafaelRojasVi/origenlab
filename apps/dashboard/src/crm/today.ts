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
 * - **Otras tareas**: the open `crm.task` rows due by the end of today that are not follow-ups
 *   («Retomar: …», a call), overdue first.
 * - **Instituciones por confirmar**: the machine-proposed institutions of open cases.
 */
import { conversation } from "./caseDisplay";
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

export interface Reply {
  card: OpportunityCardData;
  at: string;
  url: string | null;
}

export function repliesToAnswer(cards: OpportunityCardData[], now: Date): Reply[] {
  const out: Reply[] = [];
  for (const card of cards) {
    if (!isOpenCase(card)) continue;
    const conv = conversation(card, now);
    if (conv.kind !== "replied" || !conv.at) continue;
    if (answeredByTask(card, conv.at, now)) continue;
    out.push({ card, at: conv.at, url: conv.url });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
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
    const email = card.last_contact?.outbound?.at ?? null;
    // A follow-up task is done once OrigenLab writes on the case's thread on or after its day:
    // nobody has to press «Hecho» — the email the sync captures is the proof.
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
    if (!task && (pending.length > 0 || (!decided && stageBasis(card) === "historical_import"))) continue;
    const sent = card.latest_revision?.sent_at ?? null;
    if (!sent && !task) continue;
    if (!task && conversation(card, now).kind === "replied") continue;
    const byEmail = email !== null && (!sent || Date.parse(email) > Date.parse(sent));
    const touch = byEmail ? (email as string) : sent;
    const days = touch ? Math.max(0, Math.floor((now.getTime() - Date.parse(touch)) / DAY_MS)) : 0;
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

export function historicalCount(cards: OpportunityCardData[]): number {
  return cards.filter((c) => stageBasis(c) === "historical_import").length;
}

/** The two counts «Hoy»'s header shows: cases to decide and follow-ups due today. */
export function todayCounts(cards: OpportunityCardData[], now: Date = new Date()): { replies: number; decide: number; followUps: number } {
  return { replies: repliesToAnswer(cards, now).length, decide: historicalCount(cards), followUps: followUpsDue(cards, now).length };
}
