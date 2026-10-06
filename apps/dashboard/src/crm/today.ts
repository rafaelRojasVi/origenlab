/**
 * «Hoy»: what the open cases ask of an operator today, read from the pipeline cards. Pure — every
 * list here is computed in the browser from data the cards already carry, and nothing is stored.
 *
 * - **Tareas de hoy**: open `crm.task` rows due by the end of today (overdue first).
 * - **Te toca responder**: the client wrote after OrigenLab's last email on the case.
 * - **Seguimientos**: the 3 · 14 · 30-day rhythm, counted from OrigenLab's last touch (the quote,
 *   or a later email it sent): day 3 a first follow-up, day 14 a second, day 30 suggests closing.
 *   A case with an open task is already planned and is left out; so is a case whose stage is only
 *   the historical import's trace — those are decided in bulk («Decidir casos»).
 * - **Instituciones por confirmar**: the machine-proposed institutions of open cases.
 */
import { conversation } from "./caseDisplay";
import type { OpenTask, OpportunityCardData } from "./crmTypes";
import { stageBasis } from "./stage";

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

export function tasksDue(cards: OpportunityCardData[], now: Date): DueTask[] {
  const end = endOfDay(now).getTime();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const out: DueTask[] = [];
  for (const card of cards) {
    if (!isOpenCase(card)) continue;
    for (const task of card.open_tasks ?? []) {
      const due = Date.parse(task.due_at);
      if (Number.isNaN(due) || due > end) continue;
      out.push({ card, task, overdueDays: Math.max(0, Math.ceil((startOfToday - due) / DAY_MS)) });
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
    if (conv.kind === "replied" && conv.at) out.push({ card, at: conv.at, url: conv.url });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}

export type Rhythm = "primero" | "segundo" | "cerrar";

export const RHYTHM: { key: Rhythm; label: string; from: number; hint: string }[] = [
  { key: "primero", label: "Primer seguimiento", from: 3, hint: "3 a 13 días sin respuesta" },
  { key: "segundo", label: "Segundo seguimiento", from: 14, hint: "14 a 29 días sin respuesta" },
  { key: "cerrar", label: "¿Cerrar?", from: 30, hint: "30 días o más: casi nunca vuelve" },
];

export interface FollowUp {
  card: OpportunityCardData;
  /** Days since OrigenLab's last touch: the quote, or a later email it sent. */
  days: number;
  rhythm: Rhythm;
  /** The last touch was a follow-up email, not the quote itself. */
  byEmail: boolean;
}

export function followUpsDue(cards: OpportunityCardData[], now: Date): FollowUp[] {
  const out: FollowUp[] = [];
  for (const card of cards) {
    if (!isOpenCase(card) || stageBasis(card) === "historical_import") continue;
    if ((card.open_tasks ?? []).length > 0) continue;
    const sent = card.latest_revision?.sent_at ?? null;
    if (!sent) continue;
    if (conversation(card, now).kind === "replied") continue;
    const email = card.last_contact?.outbound?.at ?? null;
    const byEmail = email !== null && Date.parse(email) > Date.parse(sent);
    const touch = byEmail ? (email as string) : sent;
    const days = Math.max(0, Math.floor((now.getTime() - Date.parse(touch)) / DAY_MS));
    if (days < RHYTHM[0].from) continue;
    const rhythm = days >= RHYTHM[2].from ? "cerrar" : days >= RHYTHM[1].from ? "segundo" : "primero";
    out.push({ card, days, rhythm, byEmail });
  }
  return out.sort((a, b) => b.days - a.days);
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
