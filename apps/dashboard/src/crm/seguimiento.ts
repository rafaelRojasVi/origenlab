/**
 * «Ventas › Seguimiento»: the sheet's rows, the suggested encargado, the case score and the KPI strip.
 *
 * Pure: everything is computed in the browser from the pipeline cards and the profiles; nothing is
 * stored. Rows are open sale cases with a delivered quote revision. «Today» is the America/Santiago
 * day (`dateSantiago`); day counts are local calendar days, as Hoy counts them (`localDaysBetween`).
 */
import type { ProfileCard } from "../api/authClient";
import { isDelivered, quoteProduct } from "./caseDisplay";
import type { OpportunityCardData, PipelineResponse } from "./crmTypes";
import { PILOT_CALLS_PER_DAY, dateSantiago } from "./goals";
import { caseAmountClp } from "./nextSteps";
import { isMaskedPhone } from "./redaction";
import { localDaysBetween } from "./stage";
import { isOpenCase } from "./today";

/** Profile keys allowed to use WhatsApp; the owner confirms the real key during the local test. */
export const WHATSAPP_PROFILE_KEYS: readonly string[] = ["tatiana", "tati"];

export type FollowUpChannel = "llamada" | "correo" | "whatsapp";

export interface SegRow {
  card: OpportunityCardData;
  ownerId: string | null;
  suggestedOwnerId: string | null;
  isSuggested: boolean;
  channel: FollowUpChannel;
  amountClp: number | null;
  sentAt: string | null;
  daysSinceSent: number;
  daysSinceTouch: number;
  lastTouch: { kind: "call" | "email"; at: string; label: string } | null;
  callsToday: number;
  callsTotal: number;
  phone: string | null;
  phoneConfirmed: boolean;
  score: number;
  late: boolean;
}

export const LATE_AFTER_DAYS = 3;
const TATI_MONEY_CLP = 40_000_000;
const TATI_RULE = /hielscher|sonicad|ultrason|\buip\d|\bup\d{2,3}st\b|proyecto/i;
const EASY_RULE = /ohaus|balanza|termobalanza|frasco|matraz|tubo|filtro|masa/i;
const CONVERSATION = new Set(["contesto", "reunion"]);
const OWNER_KEYS = { tati: ["tatiana", "tati"], rafa: ["rafael", "rafa"], karla: ["karla"] } as const;
const CALL_LABEL: Record<string, string> = {
  contesto: "Llamada: contestó",
  reunion: "Llamada: reunión",
  no_contesto: "Llamada: no contestó",
};

function profileByKeys(profiles: readonly ProfileCard[], keys: readonly string[]): ProfileCard | null {
  return profiles.find((p) => keys.includes(p.key.toLowerCase())) ?? null;
}

/** The text a case can be recognised by: its title, the product and the latest quote's file and subject. */
function caseText(card: OpportunityCardData): string {
  const latest = card.latest_revision;
  return [card.title, quoteProduct(card), latest?.document?.filename, latest?.drive?.original_filename, latest?.gmail?.subject]
    .filter(Boolean)
    .join(" ");
}

function isTatiCase(card: OpportunityCardData): boolean {
  return (caseAmountClp(card) ?? 0) >= TATI_MONEY_CLP || TATI_RULE.test(caseText(card));
}

const phoneOf = (card: OpportunityCardData): string | null => {
  const phone = card.contact?.phone || null;
  return isMaskedPhone(phone) ? null : phone;
};

/** Tati for big or sonicator cases, else Rafa when there is a phone, else Karla; null when the profile is missing. */
export function suggestOwner(card: OpportunityCardData, profiles: ProfileCard[]): string | null {
  const keys = isTatiCase(card) ? OWNER_KEYS.tati : phoneOf(card) ? OWNER_KEYS.rafa : OWNER_KEYS.karla;
  return profileByKeys(profiles, keys)?.id ?? null;
}

function sentAtOf(card: OpportunityCardData): string | null {
  let best: string | null = null;
  for (const q of card.quotes) {
    for (const r of q.revisions) {
      if (isDelivered(r) && r.sent_at && (!best || Date.parse(r.sent_at) > Date.parse(best))) best = r.sent_at;
    }
  }
  return best;
}

const hasDelivered = (card: OpportunityCardData): boolean =>
  card.quotes.some((q) => q.revisions.some((r) => isDelivered(r))) || isDelivered(card.latest_revision);

/** monto 1-5 + facilidad 1-5 + interés 1-5, plus 2 when the case has a phone. */
export function scoreCase(card: OpportunityCardData): number {
  const amount = caseAmountClp(card) ?? 0;
  const monto = amount < 1_000_000 ? 1 : amount < 3_000_000 ? 2 : amount < 10_000_000 ? 3 : amount < 30_000_000 ? 4 : 5;
  const text = caseText(card);
  const facilidad = EASY_RULE.test(text) ? 5 : isTatiCase(card) ? 1 : 3;
  const sent = sentAtOf(card);
  const inbound = card.last_contact?.inbound?.at ?? null;
  const replied = !!inbound && (!sent || Date.parse(inbound) > Date.parse(sent));
  const interes = replied ? 5 : CONVERSATION.has(card.last_call_any?.outcome ?? "") ? 4 : 2;
  return monto + facilidad + interes + (phoneOf(card) ? 2 : 0);
}

function canWhatsapp(ownerId: string | null, profiles: readonly ProfileCard[]): boolean {
  const p = ownerId ? profiles.find((x) => x.id === ownerId) : null;
  return !!p && WHATSAPP_PROFILE_KEYS.includes(p.key.toLowerCase());
}

export function channelsFor(ownerId: string | null, profiles: ProfileCard[]): FollowUpChannel[] {
  return canWhatsapp(ownerId, profiles) ? ["llamada", "correo", "whatsapp"] : ["llamada", "correo"];
}

export function normalizeChannel(
  channel: FollowUpChannel | null,
  ownerId: string | null,
  profiles: ProfileCard[],
  hasPhone: boolean,
): FollowUpChannel {
  if (channel === "whatsapp") return canWhatsapp(ownerId, profiles) ? "whatsapp" : "llamada";
  if (channel) return channel;
  return hasPhone ? "llamada" : "correo";
}

/** Local calendar days from `at` to `now`: the count Hoy's rhythm and the board use (`localDaysBetween`). */
function daysSince(at: string, now: Date): number {
  return localDaysBetween(Date.parse(at), now);
}

const onSantiagoDay = (at: string | null | undefined, now: Date): boolean =>
  !!at && dateSantiago(new Date(at)) === dateSantiago(now);

/** A call or one of our emails on the Santiago day of `now`. */
export function touchedToday(row: SegRow, now: Date): boolean {
  return row.callsToday > 0 || onSantiagoDay(row.card.last_contact?.outbound?.at, now);
}

function buildRow(card: OpportunityCardData, profiles: ProfileCard[], now: Date): SegRow {
  const sentAt = sentAtOf(card);
  const outbound = card.last_contact?.outbound?.at ?? null;
  const call = card.last_call_any ?? null;
  let lastTouch: SegRow["lastTouch"] = null;
  if (call && (!outbound || Date.parse(call.at) >= Date.parse(outbound))) {
    lastTouch = { kind: "call", at: call.at, label: CALL_LABEL[call.outcome] ?? "Llamada" };
  } else if (outbound) {
    // The quote's own email, or a later one of ours: two different events, said apart (QA 2026-11-19).
    const isQuote = !!sentAt && Math.abs(Date.parse(outbound) - Date.parse(sentAt)) < 3_600_000;
    lastTouch = { kind: "email", at: outbound, label: isQuote ? "Cotización enviada" : "Correo de seguimiento" };
  }
  const touchAt = [lastTouch?.at ?? null, sentAt ?? card.created_at].reduce<string | null>(
    (best, at) => (at && (!best || Date.parse(at) > Date.parse(best)) ? at : best),
    null,
  );
  const daysSinceTouch = touchAt ? daysSince(touchAt, now) : 0;
  const ownerId = card.follow_up?.operator_id ?? null;
  const suggestedOwnerId = suggestOwner(card, profiles);
  const phone = phoneOf(card);
  return {
    card,
    ownerId,
    suggestedOwnerId,
    isSuggested: ownerId === null && suggestedOwnerId !== null,
    channel: normalizeChannel(card.follow_up?.channel ?? null, ownerId ?? suggestedOwnerId, profiles, !!phone),
    amountClp: caseAmountClp(card),
    sentAt,
    daysSinceSent: sentAt ? daysSince(sentAt, now) : 0,
    daysSinceTouch,
    lastTouch,
    callsToday: card.call_counts?.today ?? 0,
    callsTotal: card.call_counts?.total ?? 0,
    phone: card.contact?.phone || null,
    phoneConfirmed: card.contact?.phone_confirmed === true,
    score: scoreCase(card),
    late: daysSinceTouch >= LATE_AFTER_DAYS,
  };
}

/** Open sale cases with a delivered revision, late first, then score, then the longest untouched. */
export function seguimientoRows(cards: OpportunityCardData[], profiles: ProfileCard[], now: Date): SegRow[] {
  return cards
    .filter((c) => isOpenCase(c) && hasDelivered(c))
    .map((c) => buildRow(c, profiles, now))
    .sort((a, b) => Number(b.late) - Number(a.late) || b.score - a.score || b.daysSinceTouch - a.daysSinceTouch);
}

export function seguimientoKpis(
  rows: SegRow[],
  resp: PipelineResponse,
  now: Date,
): {
  callsByOperator: { operatorId: string; name: string; today: number }[];
  callsToday: number;
  goal: number;
  touchedToday: number;
  late: number;
  emailsToday: number;
  moneyClp: number;
} {
  const callsByOperator = (resp.calls_by_operator ?? []).map((o) => ({
    operatorId: o.operator_id,
    name: o.operator_name,
    today: o.today,
  }));
  return {
    callsByOperator,
    callsToday: callsByOperator.reduce((sum, o) => sum + o.today, 0),
    goal: PILOT_CALLS_PER_DAY,
    touchedToday: rows.filter((r) => touchedToday(r, now)).length,
    late: rows.filter((r) => r.late).length,
    emailsToday: rows.filter((r) => onSantiagoDay(r.card.last_contact?.outbound?.at, now)).length,
    moneyClp: rows.reduce((sum, r) => sum + (r.amountClp ?? 0), 0),
  };
}

/** The quote number a row is known by: the amount's, else the latest revision's, else the case's first. */
export function quoteNumberOf(card: OpportunityCardData): string | null {
  return (card.money ?? [])[0]?.quote_number ?? card.latest_revision?.quote_number ?? card.quote_numbers[0] ?? null;
}
