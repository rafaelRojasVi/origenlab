/**
 * How urgent each case is, for the Pipeline (owner 2026-11-11): the same próximo paso Hoy works
 * from (`planNextSteps`), read per case.
 *
 * - `atrasado`: its step is overdue; `hoy`: due today; `pronto`: within the next seven days.
 * - `luego`: nothing due this week but the case has a clock (a pause or a task further out).
 * - `sin`: an open case with no next step at all — it needs a decision, so it is flagged.
 * - `cerrado`: won, lost or closed.
 * The order (`rank`) is that list, most overdue first inside `atrasado`, then Hoy's own order.
 */
import type { OpportunityCardData } from "./crmTypes";
import { planNextSteps, type NextStep } from "./nextSteps";
import { pausedUntil } from "./stage";

export type UrgencyLevel = "atrasado" | "hoy" | "pronto" | "luego" | "sin" | "cerrado";

export interface CaseUrgency {
  level: UrgencyLevel;
  /** The case's first step this week, or null. */
  step: NextStep | null;
  /** Lower first. */
  rank: number;
}

const LEVEL_RANK: Record<UrgencyLevel, number> = { atrasado: 0, hoy: 1, pronto: 2, luego: 3, sin: 4, cerrado: 5 };

export function urgencyByCase(cards: readonly OpportunityCardData[], now: Date): Map<string, CaseUrgency> {
  const plan = planNextSteps([...cards], now, null, 7);
  const first = new Map<string, { step: NextStep; order: number }>();
  plan.steps.forEach((step, order) => {
    const id = step.card.opportunity_id;
    const had = first.get(id);
    if (!had || step.dueDays < had.step.dueDays) first.set(id, { step, order });
  });
  const out = new Map<string, CaseUrgency>();
  for (const card of cards) {
    const f = first.get(card.opportunity_id);
    let level: UrgencyLevel;
    if (card.closed_at || card.stage === "won" || card.stage === "lost" || card.stage === "abandoned") level = "cerrado";
    else if (f) level = f.step.dueDays < 0 ? "atrasado" : f.step.dueDays === 0 ? "hoy" : "pronto";
    else if (pausedUntil(card, now) || (card.open_tasks?.length ?? 0) > 0) level = "luego";
    else level = "sin";
    const within = level === "atrasado" ? (f?.step.dueDays ?? 0) * 1000 : (f?.order ?? 0);
    out.set(card.opportunity_id, { level, step: level === "cerrado" ? null : (f?.step ?? null), rank: LEVEL_RANK[level] * 1e6 + within });
  }
  return out;
}

/** Most urgent first; ties by the id so the order never jumps. */
export function byUrgency(urgency: Map<string, CaseUrgency>) {
  const r = (c: OpportunityCardData) => urgency.get(c.opportunity_id)?.rank ?? Number.MAX_SAFE_INTEGER;
  return (a: OpportunityCardData, b: OpportunityCardData) => r(a) - r(b) || a.opportunity_id.localeCompare(b.opportunity_id);
}

/** The words of an urgency badge: «6 d atrasado», «Hoy», «mañana», «jue 13», «Sin próximo paso». */
export function urgencyText(u: CaseUrgency): string {
  if (u.level === "sin") return "Sin próximo paso";
  if (!u.step) return u.level === "luego" ? "Más adelante" : "";
  const d = u.step.dueDays;
  if (d < 0) return `${-d} d atrasado`;
  if (d === 0) return "Hoy";
  if (d === 1) return "Mañana";
  return new Intl.DateTimeFormat("es-CL", { weekday: "short", day: "numeric" }).format(u.step.due).replace(".", "");
}

/** Tailwind classes per level: the card's left edge and its badge. */
export const URGENCY_EDGE: Record<UrgencyLevel, string> = {
  atrasado: "border-l-bad",
  hoy: "border-l-warn",
  pronto: "border-l-info",
  luego: "border-l-line-strong",
  sin: "border-l-warn/60",
  cerrado: "border-l-line",
};
export const URGENCY_BADGE: Record<UrgencyLevel, string> = {
  atrasado: "bg-bad-bg text-bad",
  hoy: "bg-warn-bg text-warn",
  pronto: "bg-info-bg text-info",
  luego: "bg-canvas-sunken text-ink-muted",
  sin: "border border-dashed border-warn text-warn",
  cerrado: "bg-canvas-sunken text-ink-faint",
};
