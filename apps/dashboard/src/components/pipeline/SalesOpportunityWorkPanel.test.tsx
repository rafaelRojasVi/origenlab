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
          sales_opportunity_id: SALES_ID,
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
      sales_opportunity_id: SALES_ID,
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
          sales_opportunity_id: SALES_ID,
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
      sales_opportunity_id: SALES_ID,
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
      sales_opportunity_id: SALES_ID,
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
});
