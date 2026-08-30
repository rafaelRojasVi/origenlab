/** Shared helper for smoke-v1 contact drilldown (also tested in vitest). */

export function isValidSmokeContactEmail(value: unknown): boolean {
  const s = String(value ?? "").trim();
  return s.includes("@") && !/\s/.test(s);
}

export function pickContactEmailFromLists(warm: {
  items?: Array<{ contact_email?: string }>;
}): { email: string; source: "warm_cases" } | null {
  for (const row of warm.items ?? []) {
    if (isValidSmokeContactEmail(row.contact_email)) {
      return { email: String(row.contact_email).trim(), source: "warm_cases" };
    }
  }
  return null;
}
