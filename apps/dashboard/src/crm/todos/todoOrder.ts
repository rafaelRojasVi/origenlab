/** Pure helpers of «Pendientes»: Santiago days, due labels, pasted lists and the «Subir» / «Bajar» neighbours. */
import { fmtDate } from "../ui";
import type { TodoItem } from "./todoApi";

const WEEKDAYS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];
const DAY_MS = 86_400_000;

const santiagoParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit",
});

/** Today's calendar date (ISO) in Santiago, whatever the browser's own zone. */
export function santiagoToday(now: Date): string {
  return santiagoParts.format(now);
}

const utcMs = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
const isoOf = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const weekdayOf = (iso: string) => new Date(utcMs(iso)).getUTCDay();

/** A line is late only once its day is over: due today is not late. */
export function isLate(dueOn: string | null, now: Date): boolean {
  return dueOn !== null && dueOn < santiagoToday(now);
}

/** «Hoy», «Mañana», then the rest of this week by name up to Viernes (Santiago dates). */
export function dayChoices(now: Date): { label: string; value: string }[] {
  const today = santiagoToday(now);
  const choices = [
    { label: "Hoy", value: today },
    { label: "Mañana", value: isoOf(utcMs(today) + DAY_MS) },
  ];
  for (let i = 2; i < 7; i++) {
    const value = isoOf(utcMs(today) + i * DAY_MS);
    const dow = weekdayOf(value);
    if (dow === 6 || dow === 0 || dow < weekdayOf(today)) break; // past Friday, or into next week
    choices.push({ label: WEEKDAYS[dow], value });
  }
  return choices;
}

/** «Hoy», «Mañana», the weekday within 6 days, else the date. */
export function dueLabel(dueOn: string, now: Date): string {
  const diff = Math.round((utcMs(dueOn) - utcMs(santiagoToday(now))) / DAY_MS);
  if (diff === 0) return "Hoy";
  if (diff === 1) return "Mañana";
  if (diff >= 2 && diff <= 6) return WEEKDAYS[weekdayOf(dueOn)];
  return fmtDate(`${dueOn}T12:00:00`); // local noon: no zone shifts the day
}

/** One entry per non-blank line; leading `-`, `•`, `*` and spaces stripped; order kept. */
export function splitPasted(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/^[\s\-•*]+/, "").trim())
    .filter((line) => line.length > 0);
}

const groupOf = (list: TodoItem[], id: string): { group: TodoItem[]; at: number } => {
  const target = list.find((t) => t.todo_id === id);
  if (!target) return { group: [], at: -1 };
  const group = list.filter((t) => t.is_urgent === target.is_urgent);
  return { group, at: group.findIndex((t) => t.todo_id === id) };
};

/**
 * The `after_todo_id` that moves the item UP one place within its group (same `is_urgent`, list
 * order): the id of the item two places above it. Null when it would become first (the 2nd line:
 * move to the top) — and also when it is already first or unknown, which this function cannot tell
 * apart; the caller decides "first in group" itself and disables «Subir» only then.
 */
export function neighbourAbove(list: TodoItem[], id: string): string | null {
  const { group, at } = groupOf(list, id);
  return at >= 2 ? group[at - 2].todo_id : null;
}

/**
 * The `after_todo_id` that moves the item DOWN one place: the next item of its group. Null when it
 * is already last (or unknown) — callers treat «Bajar» as disabled then.
 */
export function neighbourForMoveDown(list: TodoItem[], id: string): string | null {
  const { group, at } = groupOf(list, id);
  return at >= 0 && at < group.length - 1 ? group[at + 1].todo_id : null;
}
