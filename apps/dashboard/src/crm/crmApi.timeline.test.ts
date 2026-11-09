import { afterEach, describe, expect, it, vi } from "vitest";

import { WORKSPACE_PATHS, fetchCaseTimeline } from "./crmApi";

const CASE_ID = "96301691-af05-41ea-82e3-05f5fae40837";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchCaseTimeline", () => {
  it("reads the one allowlisted cockpit timeline path", () => {
    expect(WORKSPACE_PATHS.caseTimeline(CASE_ID)).toBe(`/v2/cockpit/opportunities/${CASE_ID}/timeline`);
  });

  it("GETs the timeline and returns its entries", async () => {
    const body = {
      opportunity_id: CASE_ID,
      entries: [
        {
          entry_kind: "domain_event",
          occurred_at: "2026-05-01T10:00:00-04:00",
          event_type: "opportunity.staged",
          aggregate_kind: "opportunity",
          aggregate_id: CASE_ID,
          actor_operator_id: null,
          actor_display_name: null,
          payload: { from_stage: "lead", to_stage: "qualifying" },
          quote_id: null,
          quote_number: null,
          revision_no: null,
          pdf_sha256: null,
        },
      ],
    };
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await fetchCaseTimeline(CASE_ID);
    expect(result.entries[0].payload?.to_stage).toBe("qualifying");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(`/v2/cockpit/opportunities/${CASE_ID}/timeline`);
    expect(init?.method).toBe("GET");
  });
});
