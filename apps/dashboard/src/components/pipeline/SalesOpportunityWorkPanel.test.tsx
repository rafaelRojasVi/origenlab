import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SalesOpportunityWorkPanel } from "./SalesOpportunityWorkPanel";
import {
  cancelCommercialTask,
  completeCommercialTask,
  createCommercialActivity,
  createCommercialTask,
  fetchSalesOpportunityActivities,
  fetchSalesOpportunityTasks,
} from "../../api/commercialOperationsClient";

vi.mock("../../api/commercialOperationsClient", () => ({
  fetchSalesOpportunityActivities: vi.fn(),
  fetchSalesOpportunityTasks: vi.fn(),
  createCommercialActivity: vi.fn(),
  createCommercialTask: vi.fn(),
  completeCommercialTask: vi.fn(),
  cancelCommercialTask: vi.fn(),
}));

const SALES_ID = "sales_1";

describe("SalesOpportunityWorkPanel", () => {
  beforeEach(() => {
    vi.mocked(fetchSalesOpportunityActivities).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(fetchSalesOpportunityTasks).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createCommercialActivity).mockReset();
    vi.mocked(createCommercialTask).mockReset();
    vi.mocked(completeCommercialTask).mockReset();
    vi.mocked(cancelCommercialTask).mockReset();
  });

  it("loads and shows existing open tasks and activities", async () => {
    vi.mocked(fetchSalesOpportunityTasks).mockResolvedValue({
      items: [
        {
          task_id: "task_1",
          opportunity_id: null,
          account_id: null,
          contact_id: null,
          title: "Llamar a cliente",
          status: "open",
          priority: "normal",
          due_at: null,
          owner_key: null,
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          completed_at: null,
          created_at: "2026-08-28T12:00:00+00:00",
          updated_at: "2026-08-28T12:00:00+00:00",
        },
      ],
    });

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);

    await waitFor(() => expect(screen.getByText("Llamar a cliente")).toBeInTheDocument());
  });

  it("creates a task anchored to the sales opportunity", async () => {
    vi.mocked(createCommercialTask).mockResolvedValue({
      task_id: "task_2",
      opportunity_id: null,
      account_id: null,
      contact_id: null,
      title: "Enviar propuesta",
      status: "open",
      priority: "normal",
      due_at: null,
      owner_key: null,
      version: 1,
      created_by: "tatiana@origenlab.cl",
      updated_by: "tatiana@origenlab.cl",
      completed_at: null,
      created_at: "2026-08-28T12:00:00+00:00",
      updated_at: "2026-08-28T12:00:00+00:00",
    });

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);
    await waitFor(() => expect(fetchSalesOpportunityTasks).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Nuevo seguimiento"), { target: { value: "Enviar propuesta" } });
    fireEvent.click(screen.getByText("Agregar seguimiento"));

    await waitFor(() => expect(createCommercialTask).toHaveBeenCalledWith(
      expect.objectContaining({ sales_opportunity_id: SALES_ID, title: "Enviar propuesta" }),
      expect.any(String),
    ));
    await waitFor(() => expect(screen.getByText("Enviar propuesta")).toBeInTheDocument());
  });

  it("completing a task calls completeCommercialTask with its version", async () => {
    vi.mocked(fetchSalesOpportunityTasks).mockResolvedValue({
      items: [
        {
          task_id: "task_1",
          opportunity_id: null,
          account_id: null,
          contact_id: null,
          title: "Llamar a cliente",
          status: "open",
          priority: "normal",
          due_at: null,
          owner_key: null,
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          completed_at: null,
          created_at: "2026-08-28T12:00:00+00:00",
          updated_at: "2026-08-28T12:00:00+00:00",
        },
      ],
    });
    vi.mocked(completeCommercialTask).mockResolvedValue({
      task_id: "task_1",
      opportunity_id: null,
      account_id: null,
      contact_id: null,
      title: "Llamar a cliente",
      status: "done",
      priority: "normal",
      due_at: null,
      owner_key: null,
      version: 2,
      created_by: "tatiana@origenlab.cl",
      updated_by: "tatiana@origenlab.cl",
      completed_at: "2026-08-28T12:30:00+00:00",
      created_at: "2026-08-28T12:00:00+00:00",
      updated_at: "2026-08-28T12:30:00+00:00",
    });

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);
    await waitFor(() => expect(screen.getByText("Llamar a cliente")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Completar"));

    await waitFor(() =>
      expect(completeCommercialTask).toHaveBeenCalledWith("task_1", { expected_version: 1 }),
    );
  });

  it("creates an activity anchored to the sales opportunity", async () => {
    vi.mocked(createCommercialActivity).mockResolvedValue({
      activity_id: "act_1",
      opportunity_id: null,
      account_id: null,
      contact_id: null,
      activity_type: "call",
      occurred_at: "2026-08-28T12:00:00+00:00",
      summary: "Llamada de seguimiento",
      detail: null,
      created_by: "tatiana@origenlab.cl",
      created_at: "2026-08-28T12:00:00+00:00",
    });

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);
    await waitFor(() => expect(fetchSalesOpportunityActivities).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Resumen de actividad"), { target: { value: "Llamada de seguimiento" } });
    fireEvent.click(screen.getByText("Registrar actividad"));

    await waitFor(() =>
      expect(createCommercialActivity).toHaveBeenCalledWith(
        expect.objectContaining({ sales_opportunity_id: SALES_ID, summary: "Llamada de seguimiento" }),
        expect.any(String),
      ),
    );
  });

  it("disables submit button while saving and reuses idempotency key only for identical content", async () => {
    let resolveTask: ((value: any) => void) | null = null;
    const taskPromise = new Promise((resolve) => {
      resolveTask = resolve;
    });

    vi.mocked(createCommercialTask).mockReturnValue(taskPromise as any);

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);
    await waitFor(() => expect(fetchSalesOpportunityTasks).toHaveBeenCalled());

    // First submit
    const taskInput = screen.getByLabelText("Nuevo seguimiento") as HTMLInputElement;
    const submitButton = screen.getByText("Agregar seguimiento") as HTMLButtonElement;

    fireEvent.change(taskInput, { target: { value: "Tarea 1" } });
    fireEvent.click(submitButton);

    // Verify button is disabled while request is in flight
    await waitFor(() => expect(submitButton.disabled).toBe(true));

    // Try to click again while request is in flight (button should be disabled)
    fireEvent.change(taskInput, { target: { value: "Tarea 2" } });
    fireEvent.click(submitButton); // This won't actually fire the handler since button is disabled

    // Verify createCommercialTask was called only once (first submission)
    expect(createCommercialTask).toHaveBeenCalledTimes(1);
    const firstCall = vi.mocked(createCommercialTask).mock.calls[0];
    const firstKey = firstCall[1];

    // Resolve the first request
    resolveTask!({
      task_id: "task_new",
      opportunity_id: null,
      account_id: null,
      contact_id: null,
      title: "Tarea 1",
      status: "open",
      priority: "normal",
      due_at: null,
      owner_key: null,
      version: 1,
      created_by: "test@origenlab.cl",
      updated_by: "test@origenlab.cl",
      completed_at: null,
      created_at: "2026-08-28T12:00:00+00:00",
      updated_at: "2026-08-28T12:00:00+00:00",
    });

    // Wait for button to be enabled again
    await waitFor(() => expect(submitButton.disabled).toBe(false));

    // Now submit with different content
    fireEvent.change(taskInput, { target: { value: "Tarea 2" } });
    fireEvent.click(submitButton);

    // Verify createCommercialTask was called a second time
    await waitFor(() => expect(createCommercialTask).toHaveBeenCalledTimes(2));
    const secondCall = vi.mocked(createCommercialTask).mock.calls[1];
    const secondKey = secondCall[1];

    // Keys should be different because content changed
    expect(firstKey).not.toBe(secondKey);
  });

  it("reuses idempotency key when retrying activity with identical content", async () => {
    let resolveActivity: ((value: any) => void) | null = null;
    let rejectActivity: ((reason?: any) => void) | null = null;

    vi.mocked(createCommercialActivity).mockImplementation(() => {
      return new Promise((resolve, reject) => {
        resolveActivity = resolve;
        rejectActivity = reject;
      }) as any;
    });

    render(<SalesOpportunityWorkPanel salesOpportunityId={SALES_ID} />);
    await waitFor(() => expect(fetchSalesOpportunityActivities).toHaveBeenCalled());

    const activityInput = screen.getByLabelText("Resumen de actividad") as HTMLInputElement;
    const submitButton = screen.getByText("Registrar actividad") as HTMLButtonElement;

    // First submit
    fireEvent.change(activityInput, { target: { value: "Seguimiento importante" } });
    fireEvent.click(submitButton);

    // Verify button is disabled while request is in flight
    await waitFor(() => expect(submitButton.disabled).toBe(true));

    // Get the first idempotency key
    expect(createCommercialActivity).toHaveBeenCalledTimes(1);
    const firstCall = vi.mocked(createCommercialActivity).mock.calls[0];
    const firstKey = firstCall[1];

    // Reject the first request (simulating a network error)
    rejectActivity!(new Error("Network error"));

    // Wait for button to be enabled again
    await waitFor(() => expect(submitButton.disabled).toBe(false));

    // Verify error is shown
    await waitFor(() => expect(screen.getByText("Network error")).toBeInTheDocument());

    // Retry with identical content (not changing the input field)
    fireEvent.click(submitButton);

    // Wait for button to be disabled again
    await waitFor(() => expect(submitButton.disabled).toBe(true));

    // Verify createCommercialActivity was called a second time
    expect(createCommercialActivity).toHaveBeenCalledTimes(2);
    const secondCall = vi.mocked(createCommercialActivity).mock.calls[1];
    const secondKey = secondCall[1];

    // Keys should be IDENTICAL because content is identical
    expect(firstKey).toBe(secondKey);

    // Resolve the second attempt
    resolveActivity!({
      activity_id: "act_retry",
      opportunity_id: null,
      account_id: null,
      contact_id: null,
      activity_type: "note",
      occurred_at: "2026-08-28T12:00:00+00:00",
      summary: "Seguimiento importante",
      detail: null,
      created_by: "test@origenlab.cl",
      created_at: "2026-08-28T12:00:00+00:00",
    });

    // Wait for activity to be added to list
    await waitFor(() => expect(screen.getByText("Seguimiento importante")).toBeInTheDocument());
  });
});
