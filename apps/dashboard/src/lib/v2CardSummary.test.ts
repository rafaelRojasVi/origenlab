import { describe, expect, it } from "vitest";

import { summarizeV2Cards } from "./v2CardSummary";
import type { V2Page, V2Quote, V2ReviewSummary, V2Task } from "../api/v2Types";

const NOW = new Date("2026-09-21T12:00:00-03:00");

function task(id: string, dueAt: string | null): V2Task {
  return {
    task_id: id,
    title: `tarea ${id}`,
    due_at: dueAt,
    overdue: false,
    opportunity_id: null,
    opportunity_title: null,
    organization_name: null,
  };
}

function page<T>(items: T[], total = items.length): V2Page<T> {
  return { items, total, limit: 200, offset: 0 };
}

const REVIEW: V2ReviewSummary = {
  ambiguous_assertions: 4,
  unresolved_assertions: 172,
  machine_proposed_organizations: 1812,
  machine_proposed_contact_points: 9460,
  unattributed_contact_points: 8765,
};

describe("summarizeV2Cards", () => {
  it("returns null-safe zeros before anything has loaded", () => {
    const s = summarizeV2Cards({ tasks: null, overdue: null, quotes: null, review: null });
    expect(s.overdueCount).toBe(0);
    expect(s.todayTasks).toEqual([]);
    expect(s.quoteFollowupCount).toBe(0);
    expect(s.ambiguousCount).toBe(0);
  });

  it("never claims data is missing while a response is still outstanding", () => {
    // A loading card that asserts "not migrated" is worse than one that shows nothing:
    // the operator would read a transient state as a permanent fact.
    const s = summarizeV2Cards({
      tasks: page<V2Task>([], 0),
      overdue: null,
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.durableWorkNotMigrated).toBe(false);
  });

  it("flags not-migrated only when every durable total is zero", () => {
    const s = summarizeV2Cards({
      tasks: page<V2Task>([], 0),
      overdue: page<V2Task>([], 0),
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.durableWorkNotMigrated).toBe(true);
  });

  it("does not flag not-migrated once any durable row exists", () => {
    const s = summarizeV2Cards({
      tasks: page([task("a", "2026-09-21T09:00:00-03:00")]),
      overdue: page<V2Task>([], 0),
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.durableWorkNotMigrated).toBe(false);
  });

  it("buckets follow-ups into overdue, today and upcoming", () => {
    const s = summarizeV2Cards({
      tasks: page([
        task("past", "2026-09-19T09:00:00-03:00"),
        task("today", "2026-09-21T18:00:00-03:00"),
        task("soon", "2026-09-25T09:00:00-03:00"),
        task("none", null),
      ]),
      overdue: page([task("past", "2026-09-19T09:00:00-03:00")], 1),
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.overdueTasks.map((t) => t.task_id)).toEqual(["past"]);
    expect(s.todayTasks.map((t) => t.task_id)).toEqual(["today"]);
    expect(s.upcomingTasks.map((t) => t.task_id)).toEqual(["soon"]);
    expect(s.unscheduledTasks.map((t) => t.task_id)).toEqual(["none"]);
  });

  it("takes the overdue count from its own total, not from the bucketed page", () => {
    // The horizon request pages at 200. If the overdue count were derived from that page,
    // a backlog larger than one page would silently understate the most urgent card.
    const s = summarizeV2Cards({
      tasks: page([task("past", "2026-09-19T09:00:00-03:00")], 200),
      overdue: page([task("past", "2026-09-19T09:00:00-03:00")], 873),
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.overdueCount).toBe(873);
  });

  it("counts quotes from the response total rather than the returned page", () => {
    const s = summarizeV2Cards({
      tasks: page<V2Task>([], 0),
      overdue: page<V2Task>([], 0),
      quotes: page<V2Quote>([], 37),
      review: REVIEW,
      now: NOW,
    });
    expect(s.quoteFollowupCount).toBe(37);
  });

  it("counts only what the migration actually stopped on, and reports the backlog apart", () => {
    // Summing these would give 11,276 — a number nobody can work through, on a card that
    // could never be driven to zero.
    const s = summarizeV2Cards({
      tasks: page<V2Task>([], 0),
      overdue: page<V2Task>([], 0),
      quotes: page<V2Quote>([], 0),
      review: REVIEW,
      now: NOW,
    });
    expect(s.ambiguousCount).toBe(4);
    expect(s.machineProposedCount).toBe(1812 + 9460);
  });
});
