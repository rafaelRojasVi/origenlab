import { useCallback, useEffect, useState } from "react";

// Same shape the backend generates for every sales_opportunity_id
// (commercial_operations_service.py: f"sales_{uuid4().hex}").
const OPPORTUNITY_ID_RE = /^sales_[0-9a-f]{32}$/;

export function buildVentasDeepLinkHash(opportunityId: string): string {
  return `#/ventas?opportunity=${opportunityId}`;
}

/**
 * Extracts a valid opportunity id from a Ventas hash (either the public
 * `#/ventas` alias or the underlying `#/pipeline` section id) — or null if
 * the hash targets a different section, has no query, or the id is
 * malformed. Never throws: an invalid deep link is simply not a deep link.
 */
export function parseVentasDeepLinkOpportunityId(hash: string): string | null {
  const raw = hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const section = path.trim().toLowerCase();

  if (section !== "ventas" && section !== "pipeline") {
    return null;
  }
  if (!query) {
    return null;
  }

  const value = new URLSearchParams(query).get("opportunity");
  if (!value || !OPPORTUNITY_ID_RE.test(value)) {
    return null;
  }

  return value;
}

/** Re-reads the deep-link opportunity id on every hash change. */
export function useVentasDeepLinkOpportunityId(): string | null {
  const read = useCallback(
    () => parseVentasDeepLinkOpportunityId(typeof window !== "undefined" ? window.location.hash : ""),
    [],
  );

  const [opportunityId, setOpportunityId] = useState<string | null>(read);

  useEffect(() => {
    const onHashChange = () => setOpportunityId(read());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [read]);

  return opportunityId;
}
