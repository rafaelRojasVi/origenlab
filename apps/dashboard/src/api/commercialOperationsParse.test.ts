import { describe, expect, it } from "vitest";

import {
  parseCommercialActivityListResponse,
  parseCommercialOperatorStateReadResponse,
  parseCommercialTask,
  parseCommercialTaskListResponse,
  parseCommercialWorkQueueResponse,
} from "./commercialOperationsParse";

describe("commercial operations parsers", () => {
  it("parses an unreviewed opportunity state", () => {
    expect(
      parseCommercialOperatorStateReadResponse({
        state: null,
      }),
    ).toEqual({
      state: null,
    });
  });

  it("parses durable operator state", () => {
    const parsed =
      parseCommercialOperatorStateReadResponse({
        state: {
          opportunity_id:
            "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          confirmation_status: "confirmed",
          manual_stage: "follow_up",
          owner_key: "tatiana@origenlab.cl",
          version: 2,
          created_by: "tatiana@origenlab.cl",
          updated_by: "rafael@origenlab.cl",
          created_at: "2026-08-24T14:00:00Z",
          updated_at: "2026-08-24T15:00:00Z",
        },
      });

    expect(parsed.state?.version).toBe(2);
    expect(parsed.state?.manual_stage).toBe(
      "follow_up",
    );
  });

  it("parses activity list", () => {
    const parsed =
      parseCommercialActivityListResponse({
        items: [
          {
            activity_id: "act_1",
            opportunity_id:
              "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            account_id: null,
            contact_id: null,
            activity_type: "whatsapp",
            occurred_at: "2026-08-24T14:00:00Z",
            summary: "Cliente pidió seguimiento",
            detail: null,
            created_by: "tatiana@origenlab.cl",
            created_at: "2026-08-24T14:01:00Z",
          },
        ],
      });

    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0].activity_type).toBe(
      "whatsapp",
    );
  });

  it("parses task list", () => {
    const parsed = parseCommercialTaskListResponse({
      items: [
        {
          task_id:
            "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          opportunity_id:
            "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          account_id: null,
          contact_id: null,
          title: "Llamar cliente",
          status: "open",
          priority: "high",
          due_at: "2026-08-25T14:00:00Z",
          owner_key: "tatiana@origenlab.cl",
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          completed_at: null,
          created_at: "2026-08-24T14:00:00Z",
          updated_at: "2026-08-24T14:00:00Z",
        },
      ],
    });

    expect(parsed.items[0].status).toBe("open");
    expect(parsed.items[0].priority).toBe("high");
  });

  it("rejects an unsupported task status", () => {
    expect(() =>
      parseCommercialTask({
        task_id:
          "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        opportunity_id:
          "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        account_id: null,
        contact_id: null,
        title: "Llamar",
        status: "deleted",
        priority: "normal",
        due_at: null,
        owner_key: null,
        version: 1,
        created_by: "tatiana@origenlab.cl",
        updated_by: "tatiana@origenlab.cl",
        completed_at: null,
        created_at: "2026-08-24T14:00:00Z",
        updated_at: "2026-08-24T14:00:00Z",
      }),
    ).toThrow(/status/);
  });

  it("rejects malformed list payloads", () => {
    expect(() =>
      parseCommercialTaskListResponse({
        items: "not-an-array",
      }),
    ).toThrow(/array/);
  });
});


describe("commercial work queue parser", () => {
  it("parses populated operational queue", () => {
    const parsed =
      parseCommercialWorkQueueResponse({
        open_tasks: [
          {
            task: {
              task_id:
                "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              opportunity_id:
                "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              account_id: null,
              contact_id: null,
              title: "Llamar cliente",
              status: "open",
              priority: "urgent",
              due_at:
                "2026-08-24T15:00:00Z",
              owner_key: null,
              version: 3,
              created_by:
                "tatiana@origenlab.cl",
              updated_by:
                "tatiana@origenlab.cl",
              completed_at: null,
              created_at:
                "2026-08-24T14:00:00Z",
              updated_at:
                "2026-08-24T14:00:00Z",
            },
            contact_display_email:
              "buyer@example.cl",
            account_display_domain:
              "example.cl",
            canonical_stage:
              "quote_sent",
            machine_review_status:
              "needs_review",
          },
        ],

        review_opportunities: [
          {
            opportunity_id:
              "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            contact_display_email:
              "buyer@example.cl",
            account_display_domain:
              "example.cl",
            canonical_stage:
              "quote_sent",
            machine_review_status:
              "needs_review",
            confirmation_status:
              "needs_review",
            manual_stage:
              "follow_up",
            owner_key:
              "tatiana@origenlab.cl",
            operator_state_version: 2,
          },
        ],

        quote_followups: [],
      });

    expect(
      parsed.open_tasks[0].task.version,
    ).toBe(3);

    expect(
      parsed.review_opportunities[0]
        .operator_state_version,
    ).toBe(2);
  });


  it("rejects malformed work queue arrays", () => {
    expect(() =>
      parseCommercialWorkQueueResponse({
        open_tasks: {},
        review_opportunities: [],
        quote_followups: [],
      }),
    ).toThrow(/open_tasks/);
  });
});
