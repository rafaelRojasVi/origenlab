/**
 * The Marketing calendar, as pure functions: days in America/Santiago, friendly relative dates,
 * the month grid, and the events — each one placed on a date the CRM actually recorded.
 *
 * | kind     | date                                                        |
 * |----------|-------------------------------------------------------------|
 * | `sent`   | each real send batch: the Santiago day attempts were accepted |
 * | `frozen` | the day the audience was frozen                              |
 * | `draft`  | the day the draft was last saved                             |
 * | `planned`| the internal planned day — metadata, never a schedule        |
 *
 * A campaign that went out over two days shows two `sent` events, never one invented date.
 * Timestamps arrive in UTC and are only ever *displayed* in Santiago time.
 */

import type { CampaignSummary } from "../crmTypes";

export const SANTIAGO = "America/Santiago";
export const PLANNING_LABEL = "Planificación interna · no programa el envío";

export type EventKind = "sent" | "draft" | "frozen" | "planned";

export const EVENT_KINDS: EventKind[] = ["sent", "frozen", "draft", "planned"];

export const EVENT_LABEL: Record<EventKind, string> = {
  sent: "Enviada",
  draft: "Borrador",
  frozen: "Audiencia congelada",
  planned: "Planificada",
};

/** Colour classes per kind: a filled chip for what happened, a dashed outline for intent. */
export const EVENT_STYLE: Record<EventKind, { chip: string; dot: string }> = {
  sent: { chip: "border-brand-600 bg-brand-600 text-white", dot: "bg-brand-600" },
  frozen: { chip: "border-info/40 bg-info-bg text-info", dot: "bg-info" },
  draft: { chip: "border-line-strong bg-canvas-sunken text-ink-muted", dot: "bg-ink-faint" },
  planned: { chip: "border-dashed border-warn bg-canvas-raised text-warn", dot: "bg-warn" },
};

export interface CalendarEvent {
  id: string;
  campaignId: string;
  campaignName: string;
  kind: EventKind;
  /** YYYY-MM-DD in America/Santiago. */
  day: string;
  /** Short line under the name, e.g. «Lote 1 de 2 · 600 aceptados». */
  detail: string;
  /** HH:MM in Santiago when a time is known. */
  time: string | null;
  origin: "imported_v1" | "native_v2";
  familyIds: string[];
  status: string;
}

const dayFormatter = new Intl.DateTimeFormat("en-CA", { timeZone: SANTIAGO, year: "numeric", month: "2-digit", day: "2-digit" });
const timeFormatter = new Intl.DateTimeFormat("es-CL", { timeZone: SANTIAGO, hour: "2-digit", minute: "2-digit", hour12: false });

/** The Santiago calendar day of a UTC instant, as YYYY-MM-DD. */
export function santiagoDay(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return dayFormatter.format(d);
}

export function santiagoTime(iso: string): string {
  return timeFormatter.format(new Date(iso));
}

export function todayInSantiago(now: Date = new Date()): string {
  return santiagoDay(now);
}

function dayNumber(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86_400_000);
}

/** Whole days from `from` to `to` (both YYYY-MM-DD); positive when `to` is later. */
export function daysBetween(from: string, to: string): number {
  return dayNumber(to) - dayNumber(from);
}

/** «hoy», «ayer», «mañana», «hace 12 días», «en 5 días». */
export function relativeDay(day: string, today: string): string {
  const n = daysBetween(today, day);
  if (n === 0) return "hoy";
  if (n === -1) return "ayer";
  if (n === 1) return "mañana";
  return n < 0 ? `hace ${-n} días` : `en ${n} días`;
}

const longDay = new Intl.DateTimeFormat("es-CL", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric" });
const shortDay = new Intl.DateTimeFormat("es-CL", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
const monthTitle = new Intl.DateTimeFormat("es-CL", { timeZone: "UTC", month: "long", year: "numeric" });

const utcNoon = (day: string) => new Date(`${day}T12:00:00Z`);

export const fmtLongDay = (day: string) => longDay.format(utcNoon(day));
export const fmtShortDay = (day: string) => shortDay.format(utcNoon(day));
export const fmtMonth = (month: string) => {
  const text = monthTitle.format(utcNoon(`${month}-01`));
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** YYYY-MM of a YYYY-MM-DD, and month arithmetic on YYYY-MM. */
export const monthOf = (day: string) => day.slice(0, 7);
export function addMonths(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export interface GridDay {
  day: string;
  inMonth: boolean;
}

/** Six Monday-first weeks covering `month` (YYYY-MM). */
export function monthGrid(month: string): GridDay[][] {
  const first = `${month}-01`;
  const weekday = (utcNoon(first).getUTCDay() + 6) % 7; // Monday = 0
  const start = dayNumber(first) - weekday;
  const weeks: GridDay[][] = [];
  for (let w = 0; w < 6; w++) {
    const week: GridDay[] = [];
    for (let i = 0; i < 7; i++) {
      const date = new Date((start + w * 7 + i) * 86_400_000);
      const day = date.toISOString().slice(0, 10);
      week.push({ day, inMonth: monthOf(day) === month });
    }
    weeks.push(week);
  }
  return weeks;
}

export const WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];

const PLANNABLE = new Set(["draft", "audience_frozen"]);

export function buildEvents(campaigns: CampaignSummary[]): CalendarEvent[] {
  const events: CalendarEvent[] = [];
  for (const c of campaigns) {
    const base = {
      campaignId: c.campaign_id,
      campaignName: c.name,
      origin: c.origin ?? "native_v2",
      familyIds: (c.equipment_lines ?? []).map((l) => l.family_id),
      status: c.status,
    } as const;
    const batches = c.send_batches ?? [];
    batches.forEach((b, i) => {
      events.push({
        ...base,
        id: `${c.campaign_id}:sent:${b.day}`,
        kind: "sent",
        day: b.day,
        detail: `${batches.length > 1 ? `Lote ${i + 1} de ${batches.length} · ` : ""}${b.accepted.toLocaleString("es-CL")} aceptados`,
        time: `${santiagoTime(b.first_accepted_at)}–${santiagoTime(b.last_accepted_at)}`,
      });
    });
    if (c.audience_frozen_at) {
      events.push({
        ...base,
        id: `${c.campaign_id}:frozen`,
        kind: "frozen",
        day: santiagoDay(c.audience_frozen_at),
        detail: `Instantánea de destinatarios · v${c.version ?? 1}`,
        time: santiagoTime(c.audience_frozen_at),
      });
    }
    if (c.status === "draft") {
      const saved = c.updated_at ?? c.created_at;
      if (saved) {
        events.push({
          ...base,
          id: `${c.campaign_id}:draft`,
          kind: "draft",
          day: santiagoDay(saved),
          detail: `Borrador guardado · v${c.version ?? 1}`,
          time: santiagoTime(saved),
        });
      }
    }
    if (c.planned_for_date && PLANNABLE.has(c.status)) {
      events.push({
        ...base,
        id: `${c.campaign_id}:planned`,
        kind: "planned",
        day: c.planned_for_date,
        detail: c.status === "draft" ? "Planificada · borrador" : "Planificada · audiencia congelada",
        time: c.planned_for_at ? santiagoTime(c.planned_for_at) : null,
      });
    }
  }
  return events.sort((a, b) => a.day.localeCompare(b.day) || (a.time ?? "").localeCompare(b.time ?? ""));
}

export interface MarketingOverviewFigures {
  lastSend: { day: string; campaignName: string; daysAgo: number } | null;
  nextPlanned: { day: string; campaignName: string; campaignId: string; inDays: number; time: string | null } | null;
  sent: number;
  drafts: number;
  frozen: number;
  planned: number;
}

export function overviewFigures(campaigns: CampaignSummary[], today: string): MarketingOverviewFigures {
  let lastSend: MarketingOverviewFigures["lastSend"] = null;
  let lastInstant = "";
  let nextPlanned: MarketingOverviewFigures["nextPlanned"] = null;
  for (const c of campaigns) {
    for (const b of c.send_batches ?? []) {
      if (b.last_accepted_at > lastInstant) {
        lastInstant = b.last_accepted_at;
        const day = santiagoDay(b.last_accepted_at);
        lastSend = { day, campaignName: c.name, daysAgo: daysBetween(day, today) };
      }
    }
    if (c.planned_for_date && PLANNABLE.has(c.status) && c.planned_for_date >= today) {
      if (!nextPlanned || c.planned_for_date < nextPlanned.day) {
        nextPlanned = {
          day: c.planned_for_date,
          campaignName: c.name,
          campaignId: c.campaign_id,
          inDays: daysBetween(today, c.planned_for_date),
          time: c.planned_for_at ? santiagoTime(c.planned_for_at) : null,
        };
      }
    }
  }
  return {
    lastSend,
    nextPlanned,
    sent: campaigns.filter((c) => (c.send_batches ?? []).length > 0).length,
    drafts: campaigns.filter((c) => c.status === "draft").length,
    frozen: campaigns.filter((c) => c.status === "audience_frozen").length,
    planned: campaigns.filter((c) => c.planned_for_date && PLANNABLE.has(c.status) && c.planned_for_date >= today).length,
  };
}

/** «hace N días» for the overview, with the singular and today handled. */
export function agoText(days: number): string {
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  return `hace ${days} días`;
}

export function inText(days: number): string {
  if (days <= 0) return "hoy";
  if (days === 1) return "mañana";
  return `en ${days} días`;
}
