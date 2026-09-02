import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExistingOpportunityPicker } from "./useExistingOpportunityPicker";
import * as client from "../../api/commercialOperationsClient";

vi.mock("../../api/commercialOperationsClient");

function opportunity(overrides: Record<string, unknown> = {}) {
  return {
    sales_opportunity_id: "sales_" + "c".repeat(32),
    source_kind: "manual" as const,
    source_opportunity_id: "sales_" + "c".repeat(32),
    account_id: null,
    primary_contact_id: null,
    organization_id: null,
    primary_crm_contact_id: null,
    title: "Reactor CEAF",
    stage: "quoting" as const,
    owner_key: "op@origenlab.cl",
    version: 1,
    created_by: "op@origenlab.cl",
    updated_by: "op@origenlab.cl",
    created_at: "2026-08-30T10:00:00Z",
    updated_at: "2026-08-30T10:00:00Z",
    stage_updated_at: "2026-08-30T10:00:00Z",
    contact_display_email: null,
    account_display_domain: null,
    organization_display_name: "CEAF",
    contact_display_name: "Tatiana Rojas",
    contact_primary_email: "tatiana@ceaf.cl",
    open_task_count: 0,
    next_task_id: null,
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

describe("useExistingOpportunityPicker", () => {
  beforeEach(() => {
    vi.mocked(client.fetchSalesOpportunities).mockReset();
  });

  it("fetches up to 200 durable opportunities once on mount, no stage filter", async () => {
    vi.mocked(client.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    renderHook(() => useExistingOpportunityPicker(true));

    await waitFor(() =>
      expect(client.fetchSalesOpportunities).toHaveBeenCalledWith({ limit: 200, offset: 0 }),
    );
  });

  it("filters visibleItems client-side by title/organization/contact", async () => {
    vi.mocked(client.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });

    const { result } = renderHook(() => useExistingOpportunityPicker(true));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.visibleItems).toHaveLength(1);
    act(() => result.current.setSearchText("no-match"));
    expect(result.current.visibleItems).toHaveLength(0);
    act(() => result.current.setSearchText("ceaf"));
    expect(result.current.visibleItems).toHaveLength(1);
  });

  it("does not fetch while inactive, and re-fetches on reactivation", async () => {
    vi.mocked(client.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { rerender } = renderHook(({ active }) => useExistingOpportunityPicker(active), {
      initialProps: { active: false },
    });
    expect(client.fetchSalesOpportunities).not.toHaveBeenCalled();

    rerender({ active: true });
    await waitFor(() => expect(client.fetchSalesOpportunities).toHaveBeenCalledTimes(1));

    rerender({ active: false });
    rerender({ active: true });
    await waitFor(() => expect(client.fetchSalesOpportunities).toHaveBeenCalledTimes(2));
  });
});
