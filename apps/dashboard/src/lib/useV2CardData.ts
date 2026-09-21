/**
 * Load the four "Trabajo comercial" card values from the V2 durable core.
 *
 * Deliberately a small hook rather than another field on `DashboardDataContext`. The
 * context is the V1 operator payload, and the point of this work is that the V2 cards stop
 * depending on it — putting them back inside it would reintroduce the coupling by another
 * route.
 *
 * The four requests are issued together and settled independently: a failure of the quote
 * request must not blank the follow-up cards, because a card that shows nothing looks
 * exactly like a card that has nothing to show.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchV2QuotesToFollowUp,
  fetchV2ReviewSummary,
  fetchV2TasksDue,
} from "../api/v2Client";
import type { V2Page, V2Quote, V2ReviewSummary, V2Task } from "../api/v2Types";
import { summarizeV2Cards, type V2CardSummary } from "./v2CardSummary";

export interface V2CardData {
  summary: V2CardSummary | null;
  loading: boolean;
  /** One message per failed request, so a partial failure is visible and specific. */
  errors: string[];
  reload: () => Promise<void>;
}

function message(error: unknown, label: string): string {
  const detail = error instanceof Error ? error.message : String(error);
  return `${label}: ${detail}`;
}

export function useV2CardData(): V2CardData {
  const [tasks, setTasks] = useState<V2Page<V2Task> | null>(null);
  const [overdue, setOverdue] = useState<V2Page<V2Task> | null>(null);
  const [quotes, setQuotes] = useState<V2Page<V2Quote> | null>(null);
  const [review, setReview] = useState<V2ReviewSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);

  const reload = useCallback(async () => {
    setLoading(true);
    const failures: string[] = [];

    const results = await Promise.allSettled([
      // Wide horizon, bucketed client-side into today / upcoming.
      fetchV2TasksDue({ horizonDays: 30, limit: 200 }),
      // `horizon_days: 0` is only ever read for its exact total, so one row is enough.
      fetchV2TasksDue({ horizonDays: 0, limit: 1 }),
      fetchV2QuotesToFollowUp({ limit: 1 }),
      fetchV2ReviewSummary(),
    ]);

    const [tasksResult, overdueResult, quotesResult, reviewResult] = results;

    if (tasksResult.status === "fulfilled") setTasks(tasksResult.value);
    else failures.push(message(tasksResult.reason, "seguimientos"));

    if (overdueResult.status === "fulfilled") setOverdue(overdueResult.value);
    else failures.push(message(overdueResult.reason, "seguimientos vencidos"));

    if (quotesResult.status === "fulfilled") setQuotes(quotesResult.value);
    else failures.push(message(quotesResult.reason, "cotizaciones"));

    if (reviewResult.status === "fulfilled") setReview(reviewResult.value);
    else failures.push(message(reviewResult.reason, "revisión humana"));

    setErrors(failures);
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Only summarize once something has arrived. Summarizing four nulls would produce a
  // confident-looking set of zeros before any request has returned.
  const summary =
    tasks === null && overdue === null && quotes === null && review === null
      ? null
      : summarizeV2Cards({ tasks, overdue, quotes, review });

  return { summary, loading, errors, reload };
}
