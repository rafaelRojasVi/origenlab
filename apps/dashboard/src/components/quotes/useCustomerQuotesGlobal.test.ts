import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";
import * as client from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
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

describe("useCustomerQuotesGlobal workflow command dispatch (CRM-Q2)", () => {
  beforeEach(() => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockReset();
    vi.mocked(client.fetchDrivePendingQuotes).mockReset().mockResolvedValue(EMPTY_DRIVE_RESULT);
    vi.mocked(client.submitCustomerQuoteForReview).mockReset();
    vi.mocked(client.requestCustomerQuoteAdjustments).mockReset();
    vi.mocked(client.approveCustomerQuote).mockReset();
    vi.mocked(client.confirmCustomerQuoteSend).mockReset();
  });

  async function setup(item = globalQuoteItemFixture()) {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));
    return result;
  }

  it("dispatching submit_for_review calls the client with expected_version and replaces the item in place", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);

    const updated = { ...item.quote, version: 2, revision_status: "pending_approval" as const, board_stage: "review" as const };
    vi.mocked(client.submitCustomerQuoteForReview).mockResolvedValue(updated);

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    expect(client.submitCustomerQuoteForReview).toHaveBeenCalledWith(item.quote.quote_id, {
      expected_version: item.quote.version,
    });
    expect(result.current.items[0].quote.board_stage).toBe("review");
    expect(result.current.items[0].quote.version).toBe(2);
  });

  it("dispatching request_adjustments calls requestCustomerQuoteAdjustments", async () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "pending_approval", board_stage: "review" },
    });
    const result = await setup(item);
    vi.mocked(client.requestCustomerQuoteAdjustments).mockResolvedValue(item.quote);

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "request_adjustments");
    });

    expect(client.requestCustomerQuoteAdjustments).toHaveBeenCalledWith(item.quote.quote_id, {
      expected_version: item.quote.version,
    });
  });

  it("dispatching approve calls approveCustomerQuote", async () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "pending_approval", board_stage: "review" },
    });
    const result = await setup(item);
    vi.mocked(client.approveCustomerQuote).mockResolvedValue(item.quote);

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "approve");
    });

    expect(client.approveCustomerQuote).toHaveBeenCalledWith(item.quote.quote_id, {
      expected_version: item.quote.version,
    });
  });

  it("dispatching confirm_send calls confirmCustomerQuoteSend", async () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "approved", board_stage: "approved_to_send" },
    });
    const result = await setup(item);
    vi.mocked(client.confirmCustomerQuoteSend).mockResolvedValue(item.quote);

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "confirm_send");
    });

    expect(client.confirmCustomerQuoteSend).toHaveBeenCalledWith(item.quote.quote_id, {
      expected_version: item.quote.version,
    });
  });

  it("on a 409 conflict, sets a conflict message and refetches the queue", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);
    vi.mocked(client.submitCustomerQuoteForReview).mockRejectedValue(
      new OperatorApiError("conflict", 409),
    );

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    expect(result.current.actionError).toMatch(/otra sesión/i);
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
  });

  it("on a non-conflict error, sets a generic action error without refetching", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);
    vi.mocked(client.submitCustomerQuoteForReview).mockRejectedValue(new Error("network down"));

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    expect(result.current.actionError).toBeTruthy();
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(1);
  });

  it("clears pendingQuoteId after the dispatch settles", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);
    vi.mocked(client.submitCustomerQuoteForReview).mockResolvedValue(item.quote);

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    expect(result.current.pendingQuoteId).toBeNull();
  });

  it("ignores a second dispatch while one is already in flight", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);

    let resolveFirst!: (value: typeof item.quote) => void;
    vi.mocked(client.submitCustomerQuoteForReview).mockReturnValue(
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    );

    let firstCall!: Promise<void>;
    act(() => {
      firstCall = result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });

    expect(client.submitCustomerQuoteForReview).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst(item.quote);
      await firstCall;
    });
  });

  it("dismissActionError clears the action error", async () => {
    const item = globalQuoteItemFixture();
    const result = await setup(item);
    vi.mocked(client.submitCustomerQuoteForReview).mockRejectedValue(new Error("boom"));

    await act(async () => {
      await result.current.dispatchWorkflowCommand(item, "submit_for_review");
    });
    expect(result.current.actionError).toBeTruthy();

    act(() => result.current.dismissActionError());
    expect(result.current.actionError).toBeNull();
  });
});
