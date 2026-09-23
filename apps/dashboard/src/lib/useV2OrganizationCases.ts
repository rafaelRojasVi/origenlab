/**
 * Every commercial case one institution is part of, whatever part it holds.
 *
 * **Why this is not a filter over `useV2Relations`.** `GET /v2/cases` carries a single
 * institution per row — the one that is asking — so crossing it in the browser answers
 * "which cases does this institution *request* in", which is a different question and a
 * much smaller list. An institution that supplies or manufactures in ten cases would read
 * as involved in none of them. Participation is recorded in
 * `crm.opportunity_organization`, and `GET /v2/organizations/{id}/cases` is the route that
 * reads it: the filter runs in the database, over rows, with no inference by name or mail
 * domain anywhere in the path.
 *
 * The hook is read-only, like everything else on these screens: it issues one credentialed
 * GET and holds no command client.
 */

import { useEffect, useState } from "react";

import { fetchV2OrganizationCases } from "../api/v2Client";
import type { V2OrganizationCase } from "../api/v2Types";
import { formatMirrorLoadError } from "./humanizeApiError";

/** One page, wide enough that it is the whole answer for every realistic local dataset. */
export const V2_ORGANIZATION_CASES_PAGE_SIZE = 100;

export interface V2OrganizationCasesState {
  cases: V2OrganizationCase[];
  /** The server's own total, so a first page never reads as "there are none". */
  total: number;
  loading: boolean;
  error: string | null;
}

export function useV2OrganizationCases(
  organizationId: string | null,
): V2OrganizationCasesState {
  const [cases, setCases] = useState<V2OrganizationCase[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    /*
      No institution is a real state, not a pending one: a contact card whose channel has
      no organization has nothing to ask for. Resetting and stopping keeps the panel
      saying "sin institución" instead of "cargando" forever.
    */
    if (!organizationId) {
      setCases([]);
      setTotal(0);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchV2OrganizationCases(organizationId, {
      limit: V2_ORGANIZATION_CASES_PAGE_SIZE,
    })
      .then((page) => {
        if (!cancelled) {
          setCases(page.items);
          setTotal(page.total);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setCases([]);
          setTotal(0);
          setError(formatMirrorLoadError("Casos de la institución", caught).message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  return { cases, total, loading, error };
}
