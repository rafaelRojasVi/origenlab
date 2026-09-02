import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";
import * as client from "../../api/customerQuoteClient";

vi.mock("../../api/customerQuoteClient");

describe("useCustomerQuotesGlobal", () => {
  it("fetches on mount with limit 200 and no filters", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledWith({
      stage: undefined,
      driveStatus: undefined,
      limit: 200,
      offset: 0,
    });
  });

  it("surfaces a load error without throwing", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeTruthy();
    expect(result.current.items).toEqual([]);
  });

  it("re-fetches with the drive-status filter when toggled", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.toggleDriveStatus("failed"));
    await waitFor(() =>
      expect(client.fetchCustomerQuotesGlobal).toHaveBeenLastCalledWith(
        expect.objectContaining({ driveStatus: ["failed"] }),
      ),
    );
  });
});
