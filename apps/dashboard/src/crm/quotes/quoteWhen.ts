import { fmtDate } from "../ui";

const DAY_MS = 86_400_000;

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * When a quote last changed, the way the list says it: «hace 5 min», «hoy 10:12», «ayer»,
 * «lun 13» within the last week, else the short date. Local time. `now` is a parameter for tests.
 */
export function quoteWhen(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const ago = now.getTime() - d.getTime();
  if (ago < 0) return fmtDate(value);
  if (ago < 60_000) return "ahora";
  if (ago < 3_600_000) return `hace ${Math.floor(ago / 60_000)} min`;
  const days = Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS);
  if (days === 0) return `hoy ${d.toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  if (days === 1) return "ayer";
  if (days < 7) {
    const weekday = d.toLocaleDateString("es-CL", { weekday: "short" }).replace(".", "");
    return `${weekday} ${d.getDate()}`;
  }
  return fmtDate(value);
}
