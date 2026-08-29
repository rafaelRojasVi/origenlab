import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSalesOpportunityBoard } from "./useSalesOpportunityBoard";
import { OperatorApiError } from "../../api/operatorClient";
import {
  fetchSalesOpportunities,
  transitionSalesOpportunityStage,
} from "../../api/commercialOperationsClient";
import type { SalesOpportunity, SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

vi.mock("../../api/commercialOperationsClient", () => ({
  fetchSalesOpportunities: vi.fn(),
  transitionSalesOpportunityStage: vi.fn(),
}));

function item(overrides: Partial<SalesOpportunityListItem> = {}): SalesOpportunityListItem {
  return {
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
    created_at: "2026-08-20T12:00:00+00:00",
    updated_at: "2026-08-25T12:00:00+00:00",
    stage_updated_at: "2026-08-25T12:00:00+00:00",
    contact_display_email: "buyer@example.cl",
    account_display_domain: "uach.cl",
    organization_display_name: null,
    contact_display_name: null,
    contact_primary_email: null,
    open_task_count: 0,
    next_task_id: null,
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

describe("useSalesOpportunityBoard", () => {
  beforeEach(() => {
    vi.mocked(fetchSalesOpportunities).mockReset();
    vi.mocked(transitionSalesOpportunityStage).mockReset();
  });

  it("loads the five active stages on mount", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item()],
    });

    const { result } = renderHook(() => useSalesOpportunityBoard());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.items).toHaveLength(1);
    expect(result.current.error).toBeNull();
    expect(vi.mocked(fetchSalesOpportunities).mock.calls[0][0]).toMatchObject({
      stage: ["new", "qualifying", "qualified", "quoting", "negotiating"],
      limit: 200,
    });
  });

  it("surfaces a friendly error on fetch failure", async () => {
    vi.mocked(fetchSalesOpportunities).mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useSalesOpportunityBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("network down");
    expect(result.current.items).toEqual([]);
  });

  it("toggleStage adds a stage to the fetch and refetches", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { result } = renderHook(() => useSalesOpportunityBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.toggleStage("won"));
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities).mock.calls).toHaveLength(2));

    expect(vi.mocked(fetchSalesOpportunities).mock.calls[1][0]).toMatchObject({
      stage: ["new", "qualifying", "qualified", "quoting", "negotiating", "won"],
    });
    expect(result.current.enabledToggles).toEqual(["won"]);
  });

  it("changeStage optimistically moves the card, then settles from the server response", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item()],
    });

    let resolveTransition: (value: SalesOpportunity) => void = () => {};
    vi.mocked(transitionSalesOpportunityStage).mockImplementation(
      () => new Promise((resolve) => { resolveTransition = resolve; }),
    );

    const { result } = renderHook(() => useSalesOpportunityBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      void result.current.changeStage(result.current.items[0], "quoting");
    });

    expect(result.current.items[0].stage).toBe("quoting");
    expect(result.current.pendingStageChangeId).toBe("sales_1");

    act(() => {
      resolveTransition({
        sales_opportunity_id: "sales_1",
        source_kind: "pr3",
        source_opportunity_id: "o_1",
        account_id: "a_1",
        primary_contact_id: "c_1",
        organization_id: null,
        primary_crm_contact_id: null,
        title: "Centrífuga refrigerada",
        stage: "quoting",
        owner_key: "tatiana@origenlab.cl",
        version: 3,
        created_by: "tatiana@origenlab.cl",
        updated_by: "tatiana@origenlab.cl",
        created_at: "2026-08-20T12:00:00+00:00",
        updated_at: "2026-08-28T12:00:00+00:00",
      });
    });

    await waitFor(() => expect(result.current.pendingStageChangeId).toBeNull());
    expect(result.current.items[0].version).toBe(3);
    expect(result.current.items[0].stage_updated_at).toBe("2026-08-28T12:00:00+00:00");
    expect(result.current.stageError).toBeNull();
  });

  it("changeStage reverts and shows the conflict message on 409, then refetches", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item()],
    });
    vi.mocked(transitionSalesOpportunityStage).mockRejectedValue(
      new OperatorApiError("conflict", 409),
    );

    const { result } = renderHook(() => useSalesOpportunityBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.changeStage(result.current.items[0], "quoting");
    });

    expect(result.current.items[0].stage).toBe("qualifying");
    expect(result.current.stageError).toBe(
      "Esta oportunidad cambió en otra sesión. Actualizamos el estado con la versión más reciente.",
    );
    expect(vi.mocked(fetchSalesOpportunities).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("changeStage shows a generic retry message on a non-conflict error", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item()],
    });
    vi.mocked(transitionSalesOpportunityStage).mockRejectedValue(new Error("Failed to fetch"));

    const { result } = renderHook(() => useSalesOpportunityBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.changeStage(result.current.items[0], "quoting");
    });

    expect(result.current.items[0].stage).toBe("qualifying");
    expect(result.current.stageError).toBe("Failed to fetch");
  });
});
