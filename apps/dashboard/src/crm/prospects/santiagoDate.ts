/** «YYYY-MM-DD» of a moment in America/Santiago — the day the API compares «Más tarde» against. */
export function santiagoDay(at: Date): string {
  return at.toLocaleDateString("en-CA", { timeZone: "America/Santiago" });
}

/** `day` plus `days` calendar days, as «YYYY-MM-DD». */
export function addDays(day: string, days: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** `day` plus one calendar month (clamped to the month's last day), as «YYYY-MM-DD». */
export function addMonth(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(d, last), 12)).toISOString().slice(0, 10);
}
