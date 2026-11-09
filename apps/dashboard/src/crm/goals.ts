/**
 * «Franja de metas»: the four goals at the top of «Hoy» — what was invoiced this month against the
 * monthly target and the pace it now asks for, the quotes that went out and nobody chased, the
 * requests still waiting for a quote and how fast quotes go out, and the team's calls this week.
 *
 * Pure: every figure is computed in the browser from the pipeline cards, the SII register and the
 * calls per day, and nothing is stored. Days are Santiago days (`YYYY-MM-DD`).
 */
import type { CallOutcome } from "./callOutcome";
import type { CallDay, OpportunityCardData } from "./crmTypes";
import type { Invoice } from "./pagos/invoiceApi";
import { type QueueItem } from "./today";

/** The monthly sales target, net, in CLP — until it is a setting (`finance.monthly_target`). */
export const MONTHLY_TARGET_CLP = 6_000_000;
/** The pilot's targets: calls per business day, and conversations in 30 days. */
export const PILOT_CALLS_PER_DAY = 25;
export const PILOT_CONVERSATIONS_30D = 50;
/** The call outcomes that are a conversation with the client. */
export const CONVERSATION_OUTCOMES: ReadonlySet<CallOutcome> = new Set<CallOutcome>(["contesto", "reunion"]);

/** The Pipeline views a goal tile opens (`#/ventas/pipeline?vista=…`). */
export type BoardView = "seguimiento" | "cotizar";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

/* ──────────────────────────────────────────────────────────────── days ── */

/** `now` as a Santiago calendar day, `YYYY-MM-DD`. */
export function dateSantiago(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** A `YYYY-MM-DD` day as a UTC-noon date, so day arithmetic never crosses a zone shift. */
function dayDate(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12));
}

function dayString(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(day: string, n: number): string {
  const d = dayDate(day);
  d.setUTCDate(d.getUTCDate() + n);
  return dayString(d);
}

function isWeekday(d: Date): boolean {
  const w = d.getUTCDay();
  return w !== 0 && w !== 6;
}

/** Monday to Friday from `today` (inclusive) to the end of its month. Feriados are not known. */
export function businessDaysLeft(today: string): number {
  const d = dayDate(today);
  const month = d.getUTCMonth();
  let n = 0;
  while (d.getUTCMonth() === month) {
    if (isWeekday(d)) n += 1;
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return n;
}

/* ─────────────────────────────────────────────────────────────── sold ── */

/** Doc types that add to what was sold: factura, factura exenta, nota de débito. */
const SALE_DOCS = new Set([33, 34, 56]);
/** A nota de crédito takes it back. */
const CREDIT_DOC = 61;

/**
 * What was sold in `today`'s month (owner ruling 2026-11-08): net + exempt of every factura (33),
 * factura exenta (34) and nota de débito (56) issued that month, less every nota de crédito (61)
 * issued that month — each document by its own `issued_on`. `docs` is how many were counted.
 */
export function soldThisMonth(items: Invoice[], today: string): { net: number; docs: number } {
  const month = today.slice(0, 7);
  let net = 0;
  let docs = 0;
  for (const inv of items) {
    if (!inv.issued_on || inv.issued_on.slice(0, 7) !== month) continue;
    const amount = (Number(inv.net_amount) || 0) + (Number(inv.exempt_amount) || 0);
    if (SALE_DOCS.has(inv.doc_type)) net += amount;
    else if (inv.doc_type === CREDIT_DOC) net -= amount;
    else continue;
    docs += 1;
  }
  return { net, docs };
}

/** The newest `issued_on` in the register: how fresh «Vendido» is. */
export function lastIssuedOn(items: Invoice[]): string | null {
  let last: string | null = null;
  for (const inv of items) if (inv.issued_on && (!last || inv.issued_on > last)) last = inv.issued_on;
  return last;
}

/** What is left to reach `target` and what that asks per business day left (null: none left). */
export function requiredPace(sold: number, target: number, today: string): { remaining: number; days: number; perDay: number | null } {
  const remaining = Math.max(0, target - sold);
  const days = businessDaysLeft(today);
  return { remaining, days, perDay: days === 0 ? null : Math.ceil(remaining / days) };
}

/* ───────────────────────────────────────────────────────────── cotizar ── */

/**
 * The «Cotizar» rows of Hoy's whole queue (`buildParaTiQueue(items, now, null)`): how many and the
 * longest wait, so the tile agrees with the section (paused and blocked cases are out in both).
 */
export function cotizarBacklog(queue: QueueItem[]): { count: number; oldestMs: number | null } {
  let count = 0;
  let oldestMs: number | null = null;
  for (const item of queue) {
    if (item.kind !== "cotizar") continue;
    count += 1;
    if (oldestMs === null || item.waitMs > oldestMs) oldestMs = item.waitMs;
  }
  return { count, oldestMs };
}

/**
 * When the case's first quote went out: the earliest `sent_at` of a revision that is not void.
 * Every origin counts — `case_quotation.py` records each sent quote the Gmail sync finds as
 * `historical_import`, so skipping that origin would leave only Cotizador quotes.
 */
export function firstSentAt(card: OpportunityCardData): string | null {
  let first: string | null = null;
  for (const q of card.quotes ?? []) {
    for (const r of q.revisions ?? []) {
      if (r.status === "void" || !r.sent_at || Number.isNaN(Date.parse(r.sent_at))) continue;
      if (!first || Date.parse(r.sent_at) < Date.parse(first)) first = r.sent_at;
    }
  }
  return first;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * «Solicitud → cotización»: the median time from the client's request (`requested_at`, else
 * `created_at`) to the first quote sent, over the cases whose first quote went out in the last 30
 * days (`current`) and the 30 before (`previous`). A case whose first quote was sent no later
 * than the case was created is left out.
 */
export function quoteSpeed(cards: OpportunityCardData[], now: Date): {
  current: { medianMs: number | null; n: number };
  previous: { medianMs: number | null; n: number };
} {
  const t = now.getTime();
  const current: number[] = [];
  const previous: number[] = [];
  for (const card of cards) {
    const sent = firstSentAt(card);
    const asked = card.requested_at ?? card.created_at;
    if (!sent || !asked) continue;
    const sentMs = Date.parse(sent);
    // A case whose first quote went out before (or as) the case was opened was built from an
    // already-sent quote (the September import): it says nothing about how fast we quote. This
    // compares with `created_at`, not `requested_at`, which is never after it.
    if (card.created_at && sentMs <= Date.parse(card.created_at)) continue;
    const duration = sentMs - Date.parse(asked);
    if (Number.isNaN(duration) || duration < 0) continue;
    if (sentMs >= t - 30 * DAY_MS && sentMs < t) current.push(duration);
    else if (sentMs >= t - 60 * DAY_MS && sentMs < t - 30 * DAY_MS) previous.push(duration);
  }
  return {
    current: { medianMs: median(current), n: current.length },
    previous: { medianMs: median(previous), n: previous.length },
  };
}

/* ────────────────────────────────────────────────────────────── reach ── */

/**
 * «Alcance»: the team's calls today and this week (Monday to `today`) against 25 per business
 * day so far, and the conversations (contestó · reunión) in the 30 days to `today`.
 */
export function reach(calls: CallDay[], today: string): { today: number; week: number; weekTarget: number; conversations30d: number } {
  const weekday = (dayDate(today).getUTCDay() + 6) % 7; // Monday 0 … Sunday 6
  const monday = addDays(today, -weekday);
  const from30 = addDays(today, -29);
  let todayCount = 0;
  let week = 0;
  let conversations30d = 0;
  for (const c of calls) {
    const n = Number(c.count) || 0;
    if (c.day === today) todayCount += n;
    if (c.day >= monday && c.day <= today) week += n;
    if (CONVERSATION_OUTCOMES.has(c.outcome) && c.day >= from30 && c.day <= today) conversations30d += n;
  }
  return { today: todayCount, week, weekTarget: PILOT_CALLS_PER_DAY * Math.min(5, weekday + 1), conversations30d };
}

/* ──────────────────────────────────────────────────────────── address ── */

/** `?vista=seguimiento|cotizar` on the Pipeline's address; anything else is no view. */
export function boardViewFromHash(hash: string): BoardView | null {
  const at = hash.indexOf("?");
  if (at < 0) return null;
  const v = new URLSearchParams(hash.slice(at + 1)).get("vista");
  return v === "seguimiento" || v === "cotizar" ? v : null;
}

/* ───────────────────────────────────────────────────────────── format ── */

/** «$1,05 M», «$6 M»: pesos in millions, up to two decimals, Chilean comma. */
export function formatMillions(n: number): string {
  return `$${(n / 1_000_000).toLocaleString("es-CL", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} M`;
}

/** A wait: «< 1 h» · «5 h» · «1 día» · «1,5 días». */
export function formatWait(ms: number): string {
  if (ms < HOUR_MS) return "< 1 h";
  if (ms < DAY_MS) return `${Math.floor(ms / HOUR_MS)} h`;
  const days = Math.round((ms / DAY_MS) * 10) / 10;
  return `${days.toLocaleString("es-CL", { maximumFractionDigits: 1 })} ${days === 1 ? "día" : "días"}`;
}
