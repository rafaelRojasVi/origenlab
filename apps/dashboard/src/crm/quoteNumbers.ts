/**
 * Quote numbers the system knows — the CRM's quotes, the Drive case archive and the numbers the
 * captured Gmail already shows (`/v2/workspace/mail-quote-numbers`) — so the person
 * making a quotation sees the last number used and the next one, and can check a number before
 * using it. Read-only: nothing here reserves a number (that comes with quote creation in the CRM).
 *
 * A number is meant to be a five-digit correlative and a year («01246-26»), but people type and
 * file them in other shapes: «1013-26» (leading zero dropped), «012392-26» (a revision digit glued
 * on), «011728A-25» (a letter suffix), «CN01247» (as in folder names). All read as
 * (year, correlative): the first five digits after padding — the same rule the API sorts by.
 */
import type { DriveFolder, MailQuoteNumber, OpportunityCardData } from "./crmTypes";
import { santiagoDay } from "./marketing/calendar";

export interface ParsedNumber {
  year: number;
  correlative: number;
}

export type NumberSource = "CRM" | "Drive" | "Gmail";

const SOURCE_ORDER: NumberSource[] = ["CRM", "Drive", "Gmail"];

export interface KnownNumber extends ParsedNumber {
  /** As written where it was found. */
  number: string;
  /** The institution, or the case folder name when Drive knows no institution. */
  label: string | null;
  sources: NumberSource[];
}

const TYPED = /^(\d+)([A-Z]*)(?:-(\d{2}|\d{4}))?$/;

/** Year and correlative of a number as typed or filed, or null. Without a year, `defaultYear`. */
export function parseQuoteNumber(raw: string, defaultYear: number): ParsedNumber | null {
  const text = raw.trim().toUpperCase().replace(/^(CN|COT)[\s-]*/, "");
  const m = TYPED.exec(text);
  if (!m) return null;
  const [, digits, , yearText] = m;
  const correlative = Number(digits.padStart(5, "0").slice(0, 5));
  const year = yearText ? Number(yearText.slice(-2)) : defaultYear;
  return Number.isFinite(correlative) && correlative > 0 ? { year, correlative } : null;
}

export function formatQuoteNumber(year: number, correlative: number): string {
  return `${String(correlative).padStart(5, "0")}-${String(year).padStart(2, "0")}`;
}

/** Two-digit Santiago year of an instant, or null when it is not a date. */
function santiagoYear(iso: string): number | null {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Number(santiagoDay(new Date(t)).slice(2, 4));
}

/**
 * One entry per number, from the CRM's quotes, the Drive archive and Gmail, with where each was
 * seen. A Gmail token carries no year («CN01247»): it takes the Santiago year of the day the
 * number was first seen, and joins the CRM or Drive entry with the same year and correlative.
 */
export function knownQuoteNumbers(
  cards: OpportunityCardData[],
  folders: DriveFolder[],
  mail: MailQuoteNumber[] = [],
): KnownNumber[] {
  const byNumber = new Map<string, KnownNumber>();
  const add = (number: string, label: string | null, source: NumberSource) => {
    const parsed = parseQuoteNumber(number, 0);
    if (!parsed || parsed.year === 0) return;
    const entry = byNumber.get(number) ?? { ...parsed, number, label: null, sources: [] };
    entry.label = entry.label ?? label;
    if (!entry.sources.includes(source)) entry.sources.push(source);
    byNumber.set(number, entry);
  };
  for (const c of cards) for (const q of c.quotes) add(q.quote_number, c.organization?.name ?? null, "CRM");
  for (const f of folders) for (const n of f.quote_numbers) add(n, f.organization_name ?? f.case_key, "Drive");
  for (const m of mail) {
    const year = santiagoYear(m.first_seen_at);
    const parsed = year === null ? null : parseQuoteNumber(m.quote_number, year);
    if (!parsed || parsed.year === 0) continue;
    const same = [...byNumber.values()].find((e) => e.year === parsed.year && e.correlative === parsed.correlative);
    if (same) {
      if (!same.sources.includes("Gmail")) same.sources.push("Gmail");
      continue;
    }
    const entry = byNumber.get(m.quote_number) ?? { ...parsed, number: m.quote_number, label: null, sources: [] };
    if (!entry.sources.includes("Gmail")) entry.sources.push("Gmail");
    byNumber.set(m.quote_number, entry);
  }
  for (const e of byNumber.values()) e.sources.sort((a, b) => SOURCE_ORDER.indexOf(a) - SOURCE_ORDER.indexOf(b));
  return [...byNumber.values()];
}

/** The highest number of `year` the system knows, and the one after it. */
export function lastAndNext(known: KnownNumber[], year: number): { last: KnownNumber | null; next: string } {
  let last: KnownNumber | null = null;
  for (const k of known) if (k.year === year && (!last || k.correlative > last.correlative)) last = k;
  return { last, next: formatQuoteNumber(year, (last?.correlative ?? 0) + 1) };
}

/** Every known number with the same year and correlative, whatever its spelling. */
export function findUses(known: KnownNumber[], wanted: ParsedNumber): KnownNumber[] {
  return known.filter((k) => k.year === wanted.year && k.correlative === wanted.correlative);
}
