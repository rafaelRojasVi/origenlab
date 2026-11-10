/**
 * «Hoy» as an agenda: one *próximo paso* per open case, grouped by when it is due.
 *
 * This is the single model Hoy renders (owner decision 2026-11-10). It does not invent new rules:
 * today's actionable items still come from `buildParaTiQueue` (the one tested queue), and the
 * projections for the coming days come from the same follow-up clock (`followUpsDue`) and the
 * cases' open tasks. What it adds:
 *   - a group per step: `atrasado` · `hoy` · `manana` · `semana` (the next 7 days);
 *   - a pause that came back is its own kind («Vuelve de pausa» with the reason), not a bare task;
 *   - the case's amount and the task's owner, and an order by valor × urgencia within a group:
 *     the operator's own steps first, then the score.
 * Phase 2 moves this to the API (`/v2/workspace/next-steps`) with pause as a state.
 */
import type { OpportunityCardData } from "./crmTypes";
import { RHYTHM, buildParaTiQueue, followUpsDue, isFollowUpDueToday, retakeHint, type QueueItem } from "./today";

export type StepGroup = "atrasado" | "hoy" | "manana" | "semana";
export type StepKind = "responder" | "cotizar" | "seguimiento" | "pausa" | "tarea";

export interface NextStep {
  /** Stable key: the case id, plus the task id for a projected task. */
  id: string;
  kind: StepKind;
  group: StepGroup;
  card: OpportunityCardData;
  due: Date;
  /** Whole days from today to `due`: negative = overdue, 0 = today. */
  dueDays: number;
  amountClp: number | null;
  owner: string | null;
  /** Valor × urgencia; higher first inside a group. */
  score: number;
  /** Today's actionable item (its row keeps every existing action), or null for a projection. */
  item: QueueItem | null;
  pause: { reason: string; hint: string | null } | null;
  /** What the projected step is («Seguimiento 2 de 3 · día 7»). */
  label: string | null;
}

export interface StepPlan {
  steps: NextStep[];
  byGroup: Record<StepGroup, NextStep[]>;
  /** Today plus the next six days, each with how many steps fall on it. */
  days: { day: Date; count: number }[];
  overdue: number;
}

const DAY_MS = 86_400_000;
const KIND_WEIGHT: Record<StepKind, number> = { responder: 3, cotizar: 3, seguimiento: 2, pausa: 1.5, tarea: 1 };
const PAUSE_PREFIX = /^Retomar:\s*/i;

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function dayDiff(from: Date, to: Date): number {
  return Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / DAY_MS);
}

/** The case's last vigente quotation in pesos, or null (foreign currency or no quote). */
export function caseAmountClp(card: OpportunityCardData): number | null {
  const money = card.money ?? [];
  const last = money[money.length - 1];
  if (!last || last.currency !== "CLP") return null;
  const n = Number(last.net_total);
  return Number.isFinite(n) ? n : null;
}

export function isPauseTask(title: string): boolean {
  return PAUSE_PREFIX.test(title);
}

export function pauseReason(title: string): string {
  return title.replace(PAUSE_PREFIX, "").trim() || "sin motivo";
}

function groupFor(dueDays: number): StepGroup {
  if (dueDays < 0) return "atrasado";
  if (dueDays === 0) return "hoy";
  return dueDays === 1 ? "manana" : "semana";
}

function scoreFor(kind: StepKind, amount: number | null, waitDays: number): number {
  const value = 1 + Math.log1p(Math.max(0, amount ?? 0) / 1_000_000);
  const urgency = 1 + Math.max(0, waitDays) / 30;
  return KIND_WEIGHT[kind] * value * urgency;
}

function fromQueueItem(item: QueueItem, now: Date): NextStep {
  const card = item.card;
  let kind: StepKind = "tarea";
  let due = now;
  let owner: string | null = null;
  let pause: NextStep["pause"] = null;
  if (item.kind === "reply") kind = "responder";
  else if (item.kind === "cotizar") kind = "cotizar";
  else if (item.kind === "followup") kind = "seguimiento";
  else {
    const t = item.due.task;
    due = new Date(t.due_at);
    owner = t.owner ?? null;
    if (isPauseTask(t.title)) {
      kind = "pausa";
      pause = { reason: pauseReason(t.title), hint: retakeHint(t.title) };
    }
  }
  const dueDays = dayDiff(now, due);
  const amountClp = caseAmountClp(card);
  const waitDays = Math.max(item.waitMs / DAY_MS, -dueDays);
  return {
    id: card.opportunity_id, kind, group: groupFor(Math.min(dueDays, 0)), card, due, dueDays,
    amountClp, owner, score: scoreFor(kind, amountClp, waitDays), item, pause, label: null,
  };
}

/** Nobody chases a client on the weekend: a step that would fall on Saturday or Sunday waits for Monday. */
export function nextBusinessDay(d: Date): Date {
  const out = new Date(d);
  while (out.getDay() === 0 || out.getDay() === 6) out.setDate(out.getDate() + 1);
  return out;
}

function followUpLabel(stepIndex: number, from: number): string {
  return stepIndex >= 3 ? `Último correo · día ${from}` : `Seguimiento ${stepIndex + 1} de 3 · día ${from}`;
}

/** The projections: the next follow-up day of each quoted case, and the tasks due in the coming days. */
function upcoming(items: OpportunityCardData[], now: Date, taken: Set<string>, horizonDays: number): NextStep[] {
  const out: NextStep[] = [];
  for (const f of followUpsDue(items, now)) {
    const id = f.card.opportunity_id;
    if (taken.has(id) || isFollowUpDueToday(f) || f.task) continue;
    const nextIndex = RHYTHM.findIndex((r) => r.from > f.days);
    if (nextIndex < 0) continue;
    const due = nextBusinessDay(new Date(startOfDay(now).getTime() + (RHYTHM[nextIndex].from - f.days) * DAY_MS));
    const ahead = dayDiff(now, due);
    if (ahead > horizonDays) continue;
    const amountClp = caseAmountClp(f.card);
    out.push({
      id, kind: "seguimiento", group: groupFor(ahead), card: f.card, due, dueDays: ahead, amountClp, owner: null,
      score: scoreFor("seguimiento", amountClp, f.days), item: null, pause: null,
      label: followUpLabel(nextIndex, RHYTHM[nextIndex].from),
    });
    taken.add(id);
  }
  for (const card of items) {
    if (card.closed_at || taken.has(card.opportunity_id)) continue;
    for (const t of card.open_tasks ?? []) {
      const due = new Date(t.due_at);
      const ahead = dayDiff(now, due);
      if (ahead <= 0 || ahead > horizonDays) continue;
      const paused = isPauseTask(t.title);
      const amountClp = caseAmountClp(card);
      out.push({
        id: `${card.opportunity_id}:${t.task_id}`, kind: paused ? "pausa" : "tarea", group: groupFor(ahead), card, due,
        dueDays: ahead, amountClp, owner: t.owner ?? null, score: scoreFor(paused ? "pausa" : "tarea", amountClp, 0),
        item: null, pause: paused ? { reason: pauseReason(t.title), hint: retakeHint(t.title) } : null,
        label: paused ? `Vuelve de pausa · ${pauseReason(t.title)}` : t.title,
      });
      taken.add(card.opportunity_id);
      break;
    }
  }
  return out;
}

/** The queue kind a step filters as («Mi cola» and the chips speak the queue's language). */
export function queueKindOf(step: NextStep): QueueItem["kind"] {
  return step.kind === "responder" ? "reply" : step.kind === "cotizar" ? "cotizar" : step.kind === "seguimiento" ? "followup" : "task";
}

/**
 * The whole agenda for `items`, as of `now`, for the operator `me` (their steps go first).
 * `allow` narrows it to a profile's view before grouping, so the counts match what is shown.
 */
export function planNextSteps(
  items: OpportunityCardData[], now: Date, me: string | null, horizonDays = 7, allow: (s: NextStep) => boolean = () => true,
): StepPlan {
  const today = buildParaTiQueue(items, now, me).map((item) => fromQueueItem(item, now));
  const taken = new Set(today.map((s) => s.id));
  const visible = [...today, ...upcoming(items, now, taken, horizonDays)].filter(allow);
  const mine = (s: NextStep) => (me && s.owner === me ? 1 : 0);
  visible.sort((a, b) => mine(b) - mine(a) || b.score - a.score);
  const byGroup: Record<StepGroup, NextStep[]> = { atrasado: [], hoy: [], manana: [], semana: [] };
  for (const s of visible) byGroup[s.group].push(s);
  const days = Array.from({ length: 7 }, (_, i) => {
    const day = new Date(startOfDay(now).getTime() + i * DAY_MS);
    return { day, count: visible.filter((s) => s.dueDays === i).length };
  });
  return { steps: visible, byGroup, days, overdue: byGroup.atrasado.length };
}
