import { afterEach, describe, expect, it, vi } from "vitest";
import {
  commercialOpportunityDetailPath,
  fetchCommercialOpportunityDetail,
  fetchCommercialOpportunities,
} from "./operatorClient";

const item = {
  opportunity_id: "o_1",
  record_kind: "commercial_signal",
  account_id: null,
  primary_contact_id: null,
  contact_display_email: null,
  account_display_domain: null,
  source_kind: "email",
  source_key: "email:1",
  deal_key: null,
  canonical_stage: "lead",
  source_stage: "lead",
  stage_reason_code: "inquiry_seen",
  stage_confidence: "medium",
  stage_is_current: true,
  stage_is_terminal: false,
  stage_evidence_at: null,
  stage_evidence_id: null,
  first_activity_at: null,
  last_activity_at: null,
  identity_link_status: "unlinked",
  review_status: "clear",
  synced_at: null,
};

describe("commercial opportunity operator client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("builds encoded detail path", () => {
    expect(commercialOpportunityDetailPath("o:test/value")).toBe(
      "/opportunities/commercial/o%3Atest%2Fvalue",
    );
  });

  it("GETs list with supported query parameters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          meta: {
            data_source: "sqlite_pr3",
            read_only: true,
            count: 1,
            total_count: 1,
            limit: 25,
            offset: 50,
            reduced_mode: false,
            note: "",
          },
          items: [item],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await fetchCommercialOpportunities({
      limit: 25,
      offset: 50,
      canonical_stage: "qualified",
      review_status: "review",
    });

    const [url, init] = fetchMock.mock.calls[0];

    expect(String(url)).toContain("/opportunities/commercial");
    expect(String(url)).toContain("limit=25");
    expect(String(url)).toContain("offset=50");
    expect(String(url)).toContain("canonical_stage=qualified");
    expect(String(url)).toContain("review_status=review");
    expect(init).toMatchObject({ method: "GET" });
    expect(result.items).toHaveLength(1);
  });

  it("GETs lifecycle detail", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          meta: {
            data_source: "postgres_mirror",
            read_only: true,
          },
          opportunity: item,
          events: [],
          evidence: [],
          conflicts: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await fetchCommercialOpportunityDetail("o_1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/opportunities/commercial/o_1");
    expect(init).toMatchObject({ method: "GET" });
    expect(result.meta.read_only).toBe(true);
    expect(result.opportunity.opportunity_id).toBe("o_1");
  });
});
