/**
 * Time left on a tender, in Chilean time: Mercado Público closes by the Santiago clock, so «hoy»,
 * «mañana» and «en N días» are counted on the Santiago calendar whatever the browser's zone.
 * Pure — the caller passes `now`.
 */
const ZONE = "America/Santiago";
const DAY_MS = 86_400_000;

const DAY_KEY = new Intl.DateTimeFormat("en-CA", { timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit" });

/** The Santiago calendar day of an instant, as a day count (so a difference is whole days). */
function santiagoDay(at: Date): number {
  const [y, m, d] = DAY_KEY.format(at).split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
}

/** «cierra en 2 días», «cierra mañana», «cierra hoy», «cerró»; «sin fecha de cierre» without a time. */
export function closingLabel(closesAt: string | null, now: Date): string {
  if (!closesAt) return "sin fecha de cierre";
  const at = new Date(closesAt);
  if (Number.isNaN(at.getTime())) return "sin fecha de cierre";
  if (at.getTime() <= now.getTime()) return "cerró";
  const days = santiagoDay(at) - santiagoDay(now);
  if (days <= 0) return "cierra hoy";
  if (days === 1) return "cierra mañana";
  return `cierra en ${days} días`;
}

/** «21 oct 2026, 15:30» in Santiago; «—» without a time. */
export function closingWhen(closesAt: string | null): string {
  if (!closesAt) return "—";
  const at = new Date(closesAt);
  if (Number.isNaN(at.getTime())) return "—";
  const day = at.toLocaleDateString("es-CL", { timeZone: ZONE, day: "2-digit", month: "short", year: "numeric" });
  const time = at.toLocaleTimeString("es-CL", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day}, ${time}`;
}
