/**
 * Deep links into the V2 durable surfaces: `#/contactos?id=…`, `#/instituciones?id=…`,
 * `#/casos?id=…`.
 *
 * A 360 screen that cannot be linked to is a screen an operator has to re-find by search
 * every time, so the selected row lives in the URL rather than in component state.
 *
 * **The id is validated, and lower-cased, here.** The dashboard-proxy allows exactly one
 * shape under `/v2/{contacts,organizations,cases}/…` — a lower-case hexadecimal UUID — and
 * refuses everything else with a 403 that reads like an outage. Letting an arbitrary hash
 * fragment reach `fetch` would turn a mistyped link into a fake incident, so anything that
 * is not that shape is simply not a deep link and the page opens on its list.
 */

import { useCallback, useEffect, useState } from "react";

import type { DashboardSection } from "./dashboardNav";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The sections whose rows are addressable one at a time. */
export type V2DetailSection = "contactos" | "instituciones" | "casos";

export function buildV2DetailHash(section: V2DetailSection, id: string): string {
  return `#/${section}?id=${encodeURIComponent(id.toLowerCase())}`;
}

/**
 * The id a hash selects on `section`, or null.
 *
 * Never throws and never guesses: a hash for another section, a missing query, or an id
 * the proxy would refuse all return null.
 */
export function parseV2DetailId(hash: string, section: V2DetailSection): string | null {
  const raw = hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  if (path.trim().toLowerCase() !== section) {
    return null;
  }
  if (!query) {
    return null;
  }
  const value = new URLSearchParams(query).get("id");
  if (!value) {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  return UUID_RE.test(normalized) ? normalized : null;
}

/** Re-reads the selected id on every hash change, so Back closes a 360 screen. */
export function useV2DetailId(section: V2DetailSection): string | null {
  const read = useCallback(
    () => parseV2DetailId(typeof window !== "undefined" ? window.location.hash : "", section),
    [section],
  );

  const [id, setId] = useState<string | null>(read);

  useEffect(() => {
    setId(read());
    const onHashChange = () => setId(read());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [read]);

  return id;
}

/** Navigate to a 360 screen. One helper, so no caller hand-builds the query string. */
export function openV2Detail(section: V2DetailSection, id: string): void {
  window.location.hash = buildV2DetailHash(section, id);
}

/** Navigate back to a section's list, dropping the selection. */
export function closeV2Detail(section: DashboardSection): void {
  window.location.hash = `#/${section}`;
}
