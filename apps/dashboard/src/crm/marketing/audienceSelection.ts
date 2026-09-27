/**
 * Selecting audience candidates. Relevance puts someone on the list; only eligibility lets them
 * be selected. These functions are the one place that rule lives in the dashboard, so no
 * button, checkbox or "select all" can route around it.
 */

import type { AudienceInstitution, AudiencePerson, Eligibility } from "./marketingTypes";

export interface SelectableDestination {
  key: string;
  address: string;
  eligibility: Eligibility;
}

/** Every destination on screen, once per key (a person can also be an institution's destination). */
export function destinationsOf(persons: AudiencePerson[], institutions: AudienceInstitution[]): Map<string, SelectableDestination> {
  const out = new Map<string, SelectableDestination>();
  for (const p of persons) out.set(p.key, { key: p.key, address: p.address, eligibility: p.eligibility });
  for (const inst of institutions) {
    for (const d of inst.destinations) if (!out.has(d.key)) out.set(d.key, d);
  }
  return out;
}

/** Toggle one destination. An ineligible destination is never added, whatever the caller asks. */
export function toggle(selected: ReadonlySet<string>, dest: SelectableDestination | undefined): Set<string> {
  const next = new Set(selected);
  if (!dest) return next;
  if (next.has(dest.key)) next.delete(dest.key);
  else if (dest.eligibility.eligible) next.add(dest.key);
  return next;
}

/** Add every eligible destination among `keys`; ineligible ones are skipped and counted. */
export function selectAllEligible(
  selected: ReadonlySet<string>,
  keys: string[],
  all: Map<string, SelectableDestination>,
): { selected: Set<string>; skipped: number } {
  const next = new Set(selected);
  let skipped = 0;
  for (const k of keys) {
    const d = all.get(k);
    if (!d) continue;
    if (d.eligibility.eligible) next.add(k);
    else skipped += 1;
  }
  return { selected: next, skipped };
}

/**
 * The recipient list a selection would produce: eligible only, one per address. Re-checks
 * eligibility at read time, so a selection made before a block was recorded does not survive it.
 */
export function recipientList(selected: ReadonlySet<string>, all: Map<string, SelectableDestination>): SelectableDestination[] {
  const byAddress = new Map<string, SelectableDestination>();
  for (const key of selected) {
    const d = all.get(key);
    if (!d || !d.eligibility.eligible) continue;
    const addr = d.address.toLowerCase();
    if (!byAddress.has(addr)) byAddress.set(addr, d);
  }
  return [...byAddress.values()];
}
