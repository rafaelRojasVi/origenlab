/**
 * The four "Trabajo comercial" card values, computed from the V2 durable core.
 *
 * This replaces the V1 `commercialWorkQueue` mirror as the source for that grid. The card
 * labels do not change — the operator reads the same four things — but what they count is
 * now durable V2 truth rather than a rebuildable projection.
 *
 * Two deliberate choices about what the cards say:
 *
 * 1. **Counts come from `total`, never from `items.length`.** The read boundary pages at
 *    200, so counting an array would silently cap a card and understate the work. The
 *    overdue card therefore issues its own `horizon_days=0` request and reads that
 *    response's `total`, rather than counting the overdue slice of the wider page.
 * 2. **"Revisión humana" counts what actually stopped and asked** — the ambiguous
 *    assertions — and reports the much larger machine-proposed backlog in its hint. Summing
 *    the two would produce a five-figure number that is not a list of decisions anyone can
 *    work through today, and a card that cannot be driven to zero stops being read.
 */

import type { V2Page, V2Quote, V2ReviewSummary, V2Task } from "../api/v2Types";
import { commercialTaskDueBucket } from "./commercialWorkQueue";

export interface V2CardSummary {
  /**
   * Exact number of overdue follow-ups.
   *
   * Taken from a dedicated `horizon_days=0` request's `total`, not by counting
   * `overdueTasks`. The horizon request pages at 200, so with more than 200 follow-ups in
   * the window the bucketed array would cap and the card would understate the overdue work
   * — which is the one number on this grid that must never be too small.
   */
  overdueCount: number;
  /** The overdue follow-ups that fit in the fetched page, for the drill-down list. */
  overdueTasks: V2Task[];
  /** Follow-ups due today. */
  todayTasks: V2Task[];
  /** Follow-ups due after today, within the requested horizon. */
  upcomingTasks: V2Task[];
  /** Follow-ups with no due date. The boundary excludes these, so it is normally empty. */
  unscheduledTasks: V2Task[];

  /** Assertions the migration refused to decide. Small, actionable, drivable to zero. */
  ambiguousCount: number;
  /** Transcribed observations awaiting confirmation. Large; shown as context, not as the count. */
  machineProposedCount: number;

  /** Quotes sent and not yet superseded. */
  quoteFollowupCount: number;

  /**
   * True when the durable tables the task and quote cards read are entirely empty.
   *
   * That is not the same as "nothing to do", and the UI must not let the two look alike.
   * V1's durable opportunities, tasks and quotes have not been migrated yet, so a plain
   * zero here would tell the operator their workload is clear when in fact it is unread.
   */
  durableWorkNotMigrated: boolean;
}

export function summarizeV2Cards(input: {
  /** Follow-ups within the wide horizon, bucketed client-side. */
  tasks: V2Page<V2Task> | null;
  /** A `horizon_days=0` response, used only for its exact `total`. */
  overdue: V2Page<V2Task> | null;
  quotes: V2Page<V2Quote> | null;
  review: V2ReviewSummary | null;
  now?: Date;
}): V2CardSummary {
  const now = input.now ?? new Date();
  const tasks = input.tasks?.items ?? [];

  const overdueTasks: V2Task[] = [];
  const todayTasks: V2Task[] = [];
  const upcomingTasks: V2Task[] = [];
  const unscheduledTasks: V2Task[] = [];

  for (const task of tasks) {
    switch (commercialTaskDueBucket(task.due_at, now)) {
      case "overdue":
        overdueTasks.push(task);
        break;
      case "today":
        todayTasks.push(task);
        break;
      case "upcoming":
        upcomingTasks.push(task);
        break;
      default:
        unscheduledTasks.push(task);
    }
  }

  const review = input.review;
  const machineProposedCount =
    (review?.machine_proposed_organizations ?? 0) +
    (review?.machine_proposed_contact_points ?? 0);

  // "Not migrated" is a claim about the durable tables, so it is only made once both
  // responses have actually arrived. While either is still null the cards are loading, and
  // a loading card must never assert that data is missing.
  const durableWorkNotMigrated =
    input.tasks !== null &&
    input.quotes !== null &&
    input.overdue !== null &&
    input.tasks.total === 0 &&
    input.overdue.total === 0 &&
    input.quotes.total === 0;

  return {
    overdueCount: input.overdue?.total ?? overdueTasks.length,
    overdueTasks,
    todayTasks,
    upcomingTasks,
    unscheduledTasks,
    ambiguousCount: review?.ambiguous_assertions ?? 0,
    machineProposedCount,
    quoteFollowupCount: input.quotes?.total ?? 0,
    durableWorkNotMigrated,
  };
}
