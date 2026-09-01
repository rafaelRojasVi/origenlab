import { describe, expect, it } from "vitest";

import {
  parseCommercialActivityListResponse,
  parseCommercialOperatorStateReadResponse,
  parseCommercialTask,
  parseCommercialTaskListResponse,
  parseCommercialWorkQueueResponse,
  parseSalesOpportunity,
  parseSalesOpportunitiesResponse,
  parseSalesOpportunityListItem,
  parseSalesOpportunityReadResponse,
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

describe("sales opportunity parsing", () => {
  const salesOpportunityRow = {
    sales_opportunity_id: "sales_1",
    source_kind: "pr3",
    source_opportunity_id: "o_1",
    account_id: "a_1",
    primary_contact_id: "c_1",
    organization_id: null,
    primary_crm_contact_id: null,
    title: "Centrífuga refrigerada",
    stage: "qualifying",
    owner_key: "tatiana@origenlab.cl",
    version: 2,
    created_by: "tatiana@origenlab.cl",
    updated_by: "tatiana@origenlab.cl",
    created_at: "2026-08-28T12:00:00+00:00",
    updated_at: "2026-08-28T12:00:00+00:00",
  };

  it("parseSalesOpportunity parses a durable sales opportunity", () => {
    const result = parseSalesOpportunity(salesOpportunityRow);
    expect(result.sales_opportunity_id).toBe("sales_1");
    expect(result.stage).toBe("qualifying");
    expect(result.organization_id).toBeNull();
  });

  it("parseSalesOpportunity rejects an unknown stage", () => {
    expect(() =>
      parseSalesOpportunity({ ...salesOpportunityRow, stage: "invented" }),
    ).toThrow(/stage/);
  });

  it("parseSalesOpportunity parses a manually-created opportunity's source_kind", () => {
    const result = parseSalesOpportunity({
      ...salesOpportunityRow,
      source_kind: "manual",
    });
    expect(result.source_kind).toBe("manual");
  });

  it("parseSalesOpportunity rejects an unknown source_kind", () => {
    expect(() =>
      parseSalesOpportunity({ ...salesOpportunityRow, source_kind: "tender" }),
    ).toThrow(/source_kind/);
  });

  it("parseSalesOpportunityListItem parses PR3 and task enrichment, tolerating nulls", () => {
    const result = parseSalesOpportunityListItem({
      ...salesOpportunityRow,
      stage_updated_at: "2026-08-28T12:00:00+00:00",
      contact_display_email: null,
      account_display_domain: null,
      organization_display_name: null,
      contact_display_name: null,
      contact_primary_email: null,
      open_task_count: 0,
      next_task_id: null,
      next_task_title: null,
      next_task_due_at: null,
    });

    expect(result.open_task_count).toBe(0);
    expect(result.next_task_title).toBeNull();
    expect(result.contact_display_email).toBeNull();
    expect(result.organization_display_name).toBeNull();
  });

  it("parseSalesOpportunityListItem parses the resolved CRM-4A identity display fields", () => {
    const result = parseSalesOpportunityListItem({
      ...salesOpportunityRow,
      stage_updated_at: "2026-08-28T12:00:00+00:00",
      contact_display_email: "buyer@example.cl",
      account_display_domain: "example.cl",
      organization_display_name: "Example SpA",
      contact_display_name: "Ana Buyer",
      contact_primary_email: "ana@example.cl",
      open_task_count: 0,
      next_task_id: null,
      next_task_title: null,
      next_task_due_at: null,
    });

    expect(result.organization_display_name).toBe("Example SpA");
    expect(result.contact_display_name).toBe("Ana Buyer");
    expect(result.contact_primary_email).toBe("ana@example.cl");
  });

  it("parseSalesOpportunitiesResponse parses meta and items", () => {
    const result = parseSalesOpportunitiesResponse({
      meta: {
        data_source: "postgres",
        read_only: true,
        count: 1,
        total_count: 5,
        limit: 100,
        offset: 0,
      },
      items: [
        {
          ...salesOpportunityRow,
          stage_updated_at: "2026-08-28T12:00:00+00:00",
          contact_display_email: "buyer@example.cl",
          account_display_domain: "example.cl",
          organization_display_name: null,
          contact_display_name: null,
          contact_primary_email: null,
          open_task_count: 1,
          next_task_id: "task_1",
          next_task_title: "Llamar cliente",
          next_task_due_at: "2026-08-29T12:00:00+00:00",
        },
      ],
    });

    expect(result.meta.total_count).toBe(5);
    expect(result.items).toHaveLength(1);
    expect(result.items[0].next_task_title).toBe("Llamar cliente");
  });

  it("parseSalesOpportunityReadResponse parses a single item envelope", () => {
    const result = parseSalesOpportunityReadResponse({
      meta: { data_source: "postgres", read_only: true },
      item: salesOpportunityRow,
    });

    expect(result.item.sales_opportunity_id).toBe("sales_1");
  });
});
