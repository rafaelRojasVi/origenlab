import {
  describe,
  expect,
  it,
} from "vitest";

import {
  commercialTaskDueBucket,
  summarizeCommercialWorkQueue,
} from "./commercialWorkQueue";


function localIso(
  year: number,
  monthIndex: number,
  day: number,
  hour: number,
): string {
  return new Date(
    year,
    monthIndex,
    day,
    hour,
    0,
    0,
  ).toISOString();
}


describe("commercial work queue local-day bucketing", () => {
  const now = new Date(
    2026,
    7,
    24,
    11,
    30,
    0,
  );


  it("classifies relative to the operator local calendar day", () => {
    expect(
      commercialTaskDueBucket(
        localIso(
          2026,
          7,
          23,
          20,
        ),
        now,
      ),
    ).toBe("overdue");

    expect(
      commercialTaskDueBucket(
        localIso(
          2026,
          7,
          24,
          18,
        ),
        now,
      ),
    ).toBe("today");

    expect(
      commercialTaskDueBucket(
        localIso(
          2026,
          7,
          25,
          9,
        ),
        now,
      ),
    ).toBe("upcoming");

    expect(
      commercialTaskDueBucket(
        null,
        now,
      ),
    ).toBe("unscheduled");
  });


  it("summarizes task and opportunity queues", () => {
    const task = (
      taskId: string,
      dueAt: string | null,
    ) => ({
      task: {
        task_id: taskId,
        opportunity_id:
          "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        account_id: null,
        contact_id: null,
        title: taskId,
        status: "open" as const,
        priority: "normal" as const,
        due_at: dueAt,
        owner_key: null,
        version: 1,
        created_by:
          "tatiana@origenlab.cl",
        updated_by:
          "tatiana@origenlab.cl",
        completed_at: null,
        created_at:
          localIso(
            2026,
            7,
            24,
            8,
          ),
        updated_at:
          localIso(
            2026,
            7,
            24,
            8,
          ),
      },

      contact_display_email: null,
      account_display_domain:
        "example.cl",
      canonical_stage:
        "quote_sent",
      machine_review_status:
        "needs_review",
    });

    const summary =
      summarizeCommercialWorkQueue(
        {
          open_tasks: [
            task(
              "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              localIso(
                2026,
                7,
                23,
                12,
              ),
            ),
            task(
              "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              localIso(
                2026,
                7,
                24,
                18,
              ),
            ),
            task(
              "task_cccccccccccccccccccccccccccccccc",
              null,
            ),
          ],

          review_opportunities: [
            {
              opportunity_id:
                "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              contact_display_email: null,
              account_display_domain:
                "example.cl",
              canonical_stage:
                "quote_sent",
              machine_review_status:
                "needs_review",
              confirmation_status: null,
              manual_stage: null,
              owner_key: null,
              operator_state_version: null,
            },
          ],

          quote_followups: [
            {
              opportunity_id:
                "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              contact_display_email: null,
              account_display_domain:
                "example.cl",
              canonical_stage:
                "quote_sent",
              machine_review_status:
                "needs_review",
              confirmation_status: null,
              manual_stage: null,
              owner_key: null,
              operator_state_version: null,
            },
          ],
        },
        now,
      );

    expect(
      summary.overdueTasks,
    ).toHaveLength(1);

    expect(
      summary.todayTasks,
    ).toHaveLength(1);

    expect(
      summary.unscheduledTasks,
    ).toHaveLength(1);

    expect(
      summary.reviewCount,
    ).toBe(1);

    expect(
      summary.quoteFollowupCount,
    ).toBe(1);
  });
});
