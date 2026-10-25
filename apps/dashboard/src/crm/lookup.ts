/**
 * «¿Ya lo tenemos?» — where a correo, a name or a domain already is in OrigenLab
 * (`GET /v2/workspace/lookup?q=`, owner 2026-10-25). Read-only; addresses come masked for a role that
 * does not read them.
 */
import { useEffect, useState } from "react";
import { fetchJsonGet, operatorApiUrl } from "../api/operatorClient";

export interface LookupPerson {
  person_id: string;
  display_name: string;
  email?: string | null;
  organization_name: string | null;
}

export interface LookupOrganization {
  organization_id: string;
  name: string;
  domain?: string;
}

export interface LookupAddress {
  email: string;
  person_id: string | null;
  display_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
}

export interface LookupControl {
  scope: "address" | "domain";
  value_norm: string;
  kind: "block" | "prior_contact";
  reason: string | null;
}

export interface LookupProspect {
  finding_id: string;
  person_name: string | null;
  organization_name: string | null;
}

export interface LookupResult {
  query: string;
  kind: "email" | "domain" | "name" | null;
  found: boolean;
  people: LookupPerson[];
  organizations: LookupOrganization[];
  addresses: LookupAddress[];
  mail: { messages: number; last_at: string | null; addresses?: number } | null;
  controls: LookupControl[];
  prospects: LookupProspect[];
}

export const fetchLookup = (q: string) => fetchJsonGet<LookupResult>(operatorApiUrl("/v2/workspace/lookup", { q }));

/** What the API can search: a correo, a domain, or three characters of a name. */
export function searchable(q: string): boolean {
  const t = q.trim();
  return t.includes("@") ? /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(t) || /^@?[^@\s]+\.[^@\s]+$/.test(t) : t.length >= 3;
}


export type LookupState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: LookupResult }
  | { kind: "error" };

/** The lookup for what the operator is typing, after a pause; idle until the text is searchable. */
export function useLookup(q: string, delayMs = 400): LookupState {
  const [state, setState] = useState<LookupState>({ kind: "idle" });
  useEffect(() => {
    const text = q.trim();
    if (!searchable(text)) {
      setState({ kind: "idle" });
      return;
    }
    let live = true;
    setState({ kind: "loading" });
    const timer = setTimeout(() => {
      fetchLookup(text)
        .then((data) => live && setState({ kind: "ready", data }))
        .catch(() => live && setState({ kind: "error" }));
    }, delayMs);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [q, delayMs]);
  return state;
}
