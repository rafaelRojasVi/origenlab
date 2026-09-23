/**
 * The quotes still in follow-up, fetched once so a 360 screen can show the ones that
 * belong to the cases in front of it.
 *
 * **Why this is still a browser-side cross.** The read boundary has no
 * quotes-by-institution route, and `V2Quote` carries an organization *name* and no id —
 * matching on that name is exactly the identity shortcut `docs/DOMAIN.md` §2.2 forbids. So
 * one page of `/v2/quotes/followup` is fetched and joined on `opportunity_id`, which is a
 * real key, and every surface that uses it renders `relationCoverageNote` beside the
 * result so a partial answer is never read as "none".
 *
 * Cases are **not** here. They are asked for by institution, on the server, through
 * `useV2OrganizationCases` — a browser-side filter over a page of `/v2/cases` could only
 * ever find the cases an institution is *asking* in.
 */

import { useEffect, useState } from "react";

import { fetchV2QuotesToFollowUp } from "../api/v2Client";
import type { V2Quote } from "../api/v2Types";
import { formatMirrorLoadError } from "./humanizeApiError";

/** One page, wide enough that the join is complete for every realistic local dataset. */
export const V2_RELATION_PAGE_SIZE = 100;

export interface V2FollowUpQuotes {
  quotes: V2Quote[];
  total: number;
  loading: boolean;
  error: string | null;
}

export function useV2FollowUpQuotes(): V2FollowUpQuotes {
  const [quotes, setQuotes] = useState<V2Quote[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchV2QuotesToFollowUp({ limit: V2_RELATION_PAGE_SIZE })
      .then((page) => {
        if (!cancelled) {
          setQuotes(page.items);
          setTotal(page.total);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          /*
            A failed quote request must not blank the cases beside it: a panel showing
            nothing looks exactly like a panel that has nothing to show, so the two lists
            fail apart and this one says so in its own place.
          */
          setQuotes([]);
          setTotal(0);
          setError(formatMirrorLoadError("Cotizaciones", caught).message);
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
  }, []);

  return { quotes, total, loading, error };
}
