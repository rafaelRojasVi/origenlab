import type { OpportunityCardData } from "./crmTypes";

/** Up to this many days since sending, a quote is "this week": too early to chase. */
export const FOLLOW_UP_AFTER_DAYS = 7;
/** Past this many days, a quote is "older than a month": likely decided one way or the other. */
export const STALE_AFTER_DAYS = 30;

const CLOSED_STAGES = new Set(["won", "lost", "abandoned"]);
const DAY_MS = 24 * 60 * 60 * 1000;

export interface FollowUpItem {
  card: OpportunityCardData;
  sentAt: string;
  /** Whole days since the case's latest quote was sent (never negative). */
  days: number;
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
 * Open cases grouped by how long ago their latest quote was sent, longest-waiting first.
 * Read-only: nothing here knows whether the client answered; it only counts days.
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
    const days = Math.max(0, Math.floor((now.getTime() - new Date(sentAt).getTime()) / DAY_MS));
    const item = { card, sentAt, days };
    out.openWithQuote += 1;
    if (days <= FOLLOW_UP_AFTER_DAYS) out.thisWeek.push(item);
    else if (days <= STALE_AFTER_DAYS) out.followUp.push(item);
    else out.older.push(item);
  }
  const longestWaitingFirst = (a: FollowUpItem, b: FollowUpItem) =>
    b.days - a.days || a.sentAt.localeCompare(b.sentAt) || a.card.opportunity_id.localeCompare(b.card.opportunity_id);
  out.thisWeek.sort(longestWaitingFirst);
  out.followUp.sort(longestWaitingFirst);
  out.older.sort(longestWaitingFirst);
  return out;
}
