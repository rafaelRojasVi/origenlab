import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  commercialOpportunityActivitiesPath,
  commercialOpportunityOperatorStatePath,
  commercialOpportunityTasksPath,
  completeCommercialTask,
  createManualSalesOpportunity,
  fetchCommercialOpportunityOperatorState,
  fetchCommercialWorkQueue,
  setCommercialOpportunityOperatorState,
  fetchSalesOpportunities,
  fetchSalesOpportunity,
  promoteSalesOpportunity,
  salesOpportunityPath,
  transitionSalesOpportunityStage,
} from "./commercialOperationsClient";


const OPPORTUNITY_ID =
  "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const TASK_ID =
  "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

const SALES_OPPORTUNITY_ID = "sales_" + "a".repeat(32);


describe("commercial operations API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });


  it("builds exact CRM readback paths", () => {
    expect(
      commercialOpportunityOperatorStatePath(
        OPPORTUNITY_ID,
      ),
    ).toBe(
      `/operations/opportunities/${OPPORTUNITY_ID}/state`,
    );

    expect(
      commercialOpportunityActivitiesPath(
        OPPORTUNITY_ID,
      ),
    ).toBe(
      `/operations/opportunities/${OPPORTUNITY_ID}/activities`,
    );

    expect(
      commercialOpportunityTasksPath(
        OPPORTUNITY_ID,
      ),
    ).toBe(
      `/operations/opportunities/${OPPORTUNITY_ID}/tasks`,
    );
  });


  it("rejects malformed opportunity IDs before fetch", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect(() =>
      commercialOpportunityOperatorStatePath(
        "opp_1",
      ),
    ).toThrow(
      /Invalid commercial opportunity ID/,
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });


  it("reads CRM state with credentials include", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv(
      "VITE_ORIGENLAB_API_BASE_URL",
      "https://dashboard.origenlab.cl/api",
    );

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          state: null,
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result =
      await fetchCommercialOpportunityOperatorState(
        OPPORTUNITY_ID,
      );

    expect(result).toEqual({
      state: null,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] =
      fetchMock.mock.calls[0];

    expect(String(url)).toBe(
      `https://dashboard.origenlab.cl/api/operations/opportunities/${OPPORTUNITY_ID}/state`,
    );

    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe("include");
  });


  it("POSTs CRM commands without browser operator identity", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv(
      "VITE_ORIGENLAB_API_BASE_URL",
      "https://dashboard.origenlab.cl/api",
    );

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          opportunity_id: OPPORTUNITY_ID,
          confirmation_status: "confirmed",
          manual_stage: null,
          owner_key: null,
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          created_at: "2026-08-24T14:00:00Z",
          updated_at: "2026-08-24T14:00:00Z",
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    await setCommercialOpportunityOperatorState(
      OPPORTUNITY_ID,
      {
        confirmation_status: "confirmed",
        expected_version: 0,
      },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] =
      fetchMock.mock.calls[0];

    expect(String(url)).toBe(
      `https://dashboard.origenlab.cl/api/operations/opportunities/${OPPORTUNITY_ID}/state`,
    );

    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");

    const headers =
      new Headers(init?.headers);

    expect(
      headers.get("Content-Type"),
    ).toBe("application/json");

    expect(
      headers.get(
        "X-OriginLab-Operator-Email",
      ),
    ).toBeNull();

    expect(
      JSON.parse(String(init?.body)),
    ).toEqual({
      confirmation_status: "confirmed",
      expected_version: 0,
    });
  });


  it("uses optimistic version for task completion", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv(
      "VITE_ORIGENLAB_API_BASE_URL",
      "https://dashboard.origenlab.cl/api",
    );

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: TASK_ID,
          opportunity_id: OPPORTUNITY_ID,
          account_id: null,
          contact_id: null,
          title: "Follow up",
          status: "done",
          priority: "normal",
          due_at: null,
          owner_key: null,
          version: 4,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          completed_at:
            "2026-08-24T15:00:00Z",
          created_at:
            "2026-08-24T14:00:00Z",
          updated_at:
            "2026-08-24T15:00:00Z",
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    await completeCommercialTask(
      TASK_ID,
      {
        expected_version: 3,
      },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [, init] =
      fetchMock.mock.calls[0];

    expect(
      JSON.parse(String(init?.body)),
    ).toEqual({
      expected_version: 3,
    });
  });
});


describe("commercial work queue API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });


  it("GETs the bounded work queue through the same-origin API", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv(
      "VITE_ORIGENLAB_API_BASE_URL",
      "https://dashboard.origenlab.cl/api",
    );

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          open_tasks: [],
          review_opportunities: [],
          quote_followups: [],
        }),
        {
          status: 200,
          headers: {
            "Content-Type":
              "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result =
      await fetchCommercialWorkQueue(50);

    expect(result).toEqual({
      open_tasks: [],
      review_opportunities: [],
      quote_followups: [],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] =
      fetchMock.mock.calls[0];

    expect(String(url)).toBe(
      "https://dashboard.origenlab.cl/api/operations/work-queue?limit=50",
    );

    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe(
      "include",
    );
  });


  it("rejects an invalid work queue limit before fetch", () => {
    const fetchMock = vi.fn();

    vi.stubGlobal(
      "fetch",
      fetchMock,
    );

    expect(() =>
      fetchCommercialWorkQueue(0),
    ).toThrow(
      /limit must be between 1 and 200/,
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });
});


describe("commercial create idempotency", () => {
  it("sends Idempotency-Key for activity creation", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv(
      "VITE_ORIGENLAB_API_BASE_URL",
      "https://dashboard.origenlab.cl/api",
    );

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          activity_id:
            "act_cccccccccccccccccccccccccccccccc",
          opportunity_id: OPPORTUNITY_ID,
          account_id: null,
          contact_id: null,
          activity_type: "call",
          occurred_at: "2026-08-24T14:00:00Z",
          summary: "Called customer",
          detail: null,
          created_by: "tatiana@origenlab.cl",
          created_at: "2026-08-24T14:00:00Z",
        }),
        {
          status: 201,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const {
      createCommercialActivity,
    } = await import(
      "./commercialOperationsClient"
    );

    await createCommercialActivity(
      {
        opportunity_id: OPPORTUNITY_ID,
        activity_type: "call",
        occurred_at: "2026-08-24T14:00:00Z",
        summary: "Called customer",
      },
      "activity.retry-123",
    );

    const [, init] =
      fetchMock.mock.calls[0];

    const headers = new Headers(
      init?.headers,
    );

    expect(
      headers.get("Idempotency-Key"),
    ).toBe("activity.retry-123");

    expect(
      headers.get(
        "X-OriginLab-Operator-Email",
      ),
    ).toBeNull();
  });


  it("rejects malformed idempotency keys before fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const {
      createCommercialTask,
    } = await import(
      "./commercialOperationsClient"
    );

    await expect(
      createCommercialTask(
        {
          opportunity_id: OPPORTUNITY_ID,
          title: "Follow up",
          priority: "normal",
        },
        "bad key with spaces",
      ),
    ).rejects.toThrow(
      /Invalid idempotency key/,
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });
});


describe("sales opportunity API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("builds the exact sales opportunity path and rejects malformed IDs", () => {
    expect(salesOpportunityPath(SALES_OPPORTUNITY_ID)).toBe(
      `/operations/sales-opportunities/${SALES_OPPORTUNITY_ID}`,
    );
    expect(() => salesOpportunityPath("not-an-id")).toThrow(/Invalid sales opportunity ID/);
  });

  it("fetchSalesOpportunities sends repeated stage params and parses the envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
          items: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchSalesOpportunities({
      stage: ["new", "qualifying"],
      limit: 200,
    });

    expect(result.meta.limit).toBe(200);

    const requestUrl = new URL(fetchMock.mock.calls[0][0] as string);
    expect(requestUrl.searchParams.getAll("stage")).toEqual(["new", "qualifying"]);
    expect(requestUrl.searchParams.get("limit")).toBe("200");
  });

  it("promoteSalesOpportunity posts with the Idempotency-Key header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          sales_opportunity_id: SALES_OPPORTUNITY_ID,
          source_kind: "pr3",
          source_opportunity_id: "o_1",
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Centrífuga",
          stage: "new",
          owner_key: "tatiana@origenlab.cl",
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          created_at: "2026-08-28T12:00:00+00:00",
          updated_at: "2026-08-28T12:00:00+00:00",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await promoteSalesOpportunity(
      { source_opportunity_id: "o_1", title: "Centrífuga" },
      "promote-key-1",
    );

    expect(result.sales_opportunity_id).toBe(SALES_OPPORTUNITY_ID);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("promote-key-1");
  });

  it("createManualSalesOpportunity posts to /operations/sales-opportunities/manual with the idempotency key header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          sales_opportunity_id: SALES_OPPORTUNITY_ID,
          source_kind: "manual",
          source_opportunity_id: SALES_OPPORTUNITY_ID,
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Centrífuga refrigerada",
          stage: "new",
          owner_key: "tatiana@origenlab.cl",
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createManualSalesOpportunity(
      { title: "Centrífuga refrigerada" },
      "manual:test:1",
    );

    expect(result.source_kind).toBe("manual");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/operations/sales-opportunities/manual");
    const headers = init.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("manual:test:1");
  });

  it("transitionSalesOpportunityStage posts the target stage and expected_version", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          sales_opportunity_id: SALES_OPPORTUNITY_ID,
          source_kind: "pr3",
          source_opportunity_id: "o_1",
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Centrífuga",
          stage: "qualifying",
          owner_key: "tatiana@origenlab.cl",
          version: 2,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          created_at: "2026-08-28T12:00:00+00:00",
          updated_at: "2026-08-28T12:05:00+00:00",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await transitionSalesOpportunityStage(SALES_OPPORTUNITY_ID, {
      stage: "qualifying",
      expected_version: 1,
    });

    expect(result.stage).toBe("qualifying");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`${SALES_OPPORTUNITY_ID}/stage`);
    expect(JSON.parse(init.body as string)).toEqual({ stage: "qualifying", expected_version: 1 });
  });

  it("fetchSalesOpportunity returns the durable item", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          meta: { data_source: "postgres", read_only: true },
          item: {
            sales_opportunity_id: SALES_OPPORTUNITY_ID,
            source_kind: "pr3",
            source_opportunity_id: "o_1",
            account_id: null,
            primary_contact_id: null,
            organization_id: null,
            primary_crm_contact_id: null,
            title: "Centrífuga",
            stage: "won",
            owner_key: "tatiana@origenlab.cl",
            version: 5,
            created_by: "tatiana@origenlab.cl",
            updated_by: "tatiana@origenlab.cl",
            created_at: "2026-08-28T12:00:00+00:00",
            updated_at: "2026-08-28T12:05:00+00:00",
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchSalesOpportunity(SALES_OPPORTUNITY_ID);
    expect(result.item.stage).toBe("won");
  });
});
