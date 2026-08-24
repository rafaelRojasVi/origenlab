import { describe, expect, it } from "vitest";
import {
  parseCommercialOpportunitiesResponse,
  parseCommercialOpportunityDetailResponse,
} from "./commercialOpportunitiesParse";

const item = {
  opportunity_id: "o_1",
  record_kind: "commercial_signal",
  account_id: "a_1",
  primary_contact_id: "c_1",
  contact_display_email: "buyer@example.cl",
  account_display_domain: "example.cl",
  source_kind: "email",
  source_key: "email:1",
  deal_key: null,
  canonical_stage: "qualified",
  source_stage: "quote_requested",
  stage_reason_code: "quote_request",
  stage_confidence: "high",
  stage_is_current: true,
  stage_is_terminal: false,
  stage_evidence_at: "2026-08-23T00:00:00+00:00",
  stage_evidence_id: "ev_1",
  first_activity_at: "2026-08-22T00:00:00+00:00",
  last_activity_at: "2026-08-23T00:00:00+00:00",
  identity_link_status: "linked",
  review_status: "review",
  synced_at: null,
};

describe("commercial opportunities parser", () => {
  it("parses list response", () => {
    const parsed = parseCommercialOpportunitiesResponse({
      meta: {
        data_source: "sqlite_pr3",
        read_only: true,
        count: 1,
        total_count: 9577,
        limit: 50,
        offset: 0,
        reduced_mode: false,
        note: "",
      },
      items: [item],
    });

    expect(parsed.meta.total_count).toBe(9577);
    expect(parsed.items[0].contact_display_email).toBe(
      "buyer@example.cl",
    );
    expect(parsed.items[0].canonical_stage).toBe("qualified");
  });

  it("parses detail graph without interpreting provenance JSON", () => {
    const parsed = parseCommercialOpportunityDetailResponse({
      meta: {
        data_source: "postgres_mirror",
        read_only: true,
      },
      opportunity: item,
      events: [
        {
          event_id: "evt_1",
          opportunity_id: "o_1",
          canonical_event_type: "quote_requested",
          source_event_type: "quote_requested",
          event_at: "2026-08-23T00:00:00+00:00",
          source_table: "emails",
          source_record_id: "1",
          source_email_id: 1,
          source_attachment_id: null,
          confidence: "high",
          operator_confirmed: false,
          detail_json: { private_provenance: true },
          synced_at: null,
        },
      ],
      evidence: [],
      conflicts: [],
    });

    expect(parsed.meta.data_source).toBe("postgres_mirror");
    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0].detail_json).toEqual({
      private_provenance: true,
    });
  });

  it("rejects unknown data sources", () => {
    expect(() =>
      parseCommercialOpportunitiesResponse({
        meta: {
          data_source: "unexpected",
          read_only: true,
          count: 0,
          total_count: 0,
          limit: 50,
          offset: 0,
          reduced_mode: false,
          note: "",
        },
        items: [],
      }),
    ).toThrow(/data_source/);
  });

  it("rejects malformed list items", () => {
    expect(() =>
      parseCommercialOpportunitiesResponse({
        meta: {
          data_source: "sqlite_pr3",
          read_only: true,
          count: 1,
          total_count: 1,
          limit: 50,
          offset: 0,
          reduced_mode: false,
          note: "",
        },
        items: [{ opportunity_id: 123 }],
      }),
    ).toThrow();
  });
});
