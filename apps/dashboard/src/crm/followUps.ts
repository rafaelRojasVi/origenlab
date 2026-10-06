import type { OpportunityCardData } from "./crmTypes";

/** Up to this many days since sending, a quote is "this week": too early to chase. */
export const FOLLOW_UP_AFTER_DAYS = 7;
/** Past this many days, a quote is "older than a month": likely decided one way or the other. */
export const STALE_AFTER_DAYS = 30;

const CLOSED_STAGES = new Set(["won", "lost", "abandoned"]);
const DAY_MS = 24 * 60 * 60 * 1000;

export interface FollowUpItem {
  card: OpportunityCardData;
  /** When the latest quote was sent. */
  sentAt: string;
  /** The last time OrigenLab touched the case: the quote, or a later email it sent on the thread. */
  lastTouchAt: string;
  /** What `lastTouchAt` is: the quote itself, or a follow-up email. */
  lastTouch: "quote" | "email";
  /** Whole days since `lastTouchAt` (never negative). */
  days: number;
  /** The client wrote after OrigenLab's last touch: the next move is ours. */
  replied: boolean;
}

export interface FollowUps {
  thisWeek: FollowUpItem[];
  followUp: FollowUpItem[];
  older: FollowUpItem[];
  /** Open cases with no sent quote yet. */
  withoutQuote: OpportunityCardData[];
  openWithQuote: number;
  /** The newest send date among all cases: how far the imported data reaches. */
  latestSentAt: string | null;
}

export function isOpen(card: OpportunityCardData): boolean {
  return !card.closed_at && !CLOSED_STAGES.has(card.stage);
}

/**
 * Open cases grouped by how long ago OrigenLab last touched them, longest-waiting first.
 *
 * The last touch is the latest quote sent, or a later email OrigenLab sent on one of the case's
 * Gmail threads (`last_contact.outbound`, from the mail capture) — a follow-up resets the clock
 * without anyone recording it. `replied` marks a case whose client wrote after that touch.
 * Read-only: it only reads dates.
 */
export function groupFollowUps(cards: OpportunityCardData[], now: Date): FollowUps {
  const out: FollowUps = { thisWeek: [], followUp: [], older: [], withoutQuote: [], openWithQuote: 0, latestSentAt: null };
  for (const card of cards) {
    const sentAt = card.latest_revision?.sent_at ?? null;
    if (sentAt && (out.latestSentAt === null || sentAt > out.latestSentAt)) out.latestSentAt = sentAt;
    if (!isOpen(card)) continue;
    if (!sentAt) {
      out.withoutQuote.push(card);
      continue;
    }
    const email = card.last_contact?.outbound?.at ?? null;
    const byEmail = email !== null && Date.parse(email) > Date.parse(sentAt);
    const lastTouchAt = byEmail ? email : sentAt;
    const inbound = card.last_contact?.inbound?.at ?? null;
    const replied = inbound !== null && Date.parse(inbound) > Date.parse(lastTouchAt);
    const days = Math.max(0, Math.floor((now.getTime() - Date.parse(lastTouchAt)) / DAY_MS));
    const item: FollowUpItem = { card, sentAt, lastTouchAt, lastTouch: byEmail ? "email" : "quote", days, replied };
    out.openWithQuote += 1;
    if (days <= FOLLOW_UP_AFTER_DAYS) out.thisWeek.push(item);
    else if (days <= STALE_AFTER_DAYS) out.followUp.push(item);
    else out.older.push(item);
  }
  // A client who answered comes first: the next move is ours. Then the longest waiting.
  const longestWaitingFirst = (a: FollowUpItem, b: FollowUpItem) =>
    Number(b.replied) - Number(a.replied) ||
    b.days - a.days || a.lastTouchAt.localeCompare(b.lastTouchAt) || a.card.opportunity_id.localeCompare(b.card.opportunity_id);
  out.thisWeek.sort(longestWaitingFirst);
  out.followUp.sort(longestWaitingFirst);
  out.older.sort(longestWaitingFirst);
  return out;
}
