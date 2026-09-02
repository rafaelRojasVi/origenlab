import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";
import * as client from "../../api/customerQuoteClient";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../../test/fixtures/customerQuoteFixtures";

vi.mock("../../api/customerQuoteClient");

const EMPTY_CRM_RESULT = { meta: { count: 0, total_count: 0, limit: 200, offset: 0 }, items: [] };
const EMPTY_DRIVE_RESULT = { meta: { count: 0 }, items: [] };

describe("useCustomerQuotesGlobal", () => {
  beforeEach(() => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockReset();
    vi.mocked(client.fetchDrivePendingQuotes).mockReset().mockResolvedValue(EMPTY_DRIVE_RESULT);
  });

  it("fetches on mount with limit 200 and no filters", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue(EMPTY_CRM_RESULT);

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledWith({
      stage: undefined,
      driveStatus: undefined,
      limit: 200,
      offset: 0,
    });
    expect(client.fetchDrivePendingQuotes).toHaveBeenCalledTimes(1);
  });

  it("surfaces a load error without throwing", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeTruthy();
    expect(result.current.items).toEqual([]);
  });

  it("re-fetches with the drive-status filter when toggled", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue(EMPTY_CRM_RESULT);

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.toggleDriveStatus("failed"));
    await waitFor(() =>
      expect(client.fetchCustomerQuotesGlobal).toHaveBeenLastCalledWith(
        expect.objectContaining({ driveStatus: ["failed"] }),
      ),
    );
  });

  it("merges CRM and Drive-only rows into one queue", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [drivePendingQuoteItemFixture()],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.rows).toHaveLength(2);
    expect(result.current.rows.map((row) => row.kind).sort()).toEqual(["crm", "drive_pending"]);
    expect(result.current.isEmpty).toBe(false);
  });

  it("isEmpty requires both the CRM list and the Drive-pending list to be empty", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue(EMPTY_CRM_RESULT);
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [drivePendingQuoteItemFixture()],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isEmpty).toBe(false);
    expect(result.current.rows).toHaveLength(1);
  });

  it("a failing Drive fetch surfaces an error but keeps the CRM rows visible", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockRejectedValue(new Error("drive down"));

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBeTruthy();
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0].kind).toBe("crm");
  });

  it("refetch refreshes both sources", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue(EMPTY_CRM_RESULT);

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.refetch();
    });

    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
    expect(client.fetchDrivePendingQuotes).toHaveBeenCalledTimes(2);
  });
});
