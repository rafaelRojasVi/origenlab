import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SalesOpportunityWorkspaceDrawer } from "./SalesOpportunityWorkspaceDrawer";
import { fetchSalesOpportunity, transitionSalesOpportunityStage } from "../../api/commercialOperationsClient";
import { OperatorApiError } from "../../api/operatorClient";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

vi.mock("../../api/commercialOperationsClient", () => ({
  fetchSalesOpportunity: vi.fn(),
  transitionSalesOpportunityStage: vi.fn(),
}));

vi.mock("./SalesOpportunityWorkPanel", () => ({
  SalesOpportunityWorkPanel: () => <div data-testid="work-panel-stub" />,
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
    open_task_count: 0,
    next_task_id: null,
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

describe("SalesOpportunityWorkspaceDrawer", () => {
  beforeEach(() => {
    vi.mocked(fetchSalesOpportunity).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true },
      item: { ...item(), version: 2 },
    });
    vi.mocked(transitionSalesOpportunityStage).mockReset();
  });

  it("renders nothing when closed", () => {
    render(<SalesOpportunityWorkspaceDrawer item={item()} open={false} onClose={vi.fn()} onStageChanged={vi.fn()} />);
    expect(screen.queryByTestId("sales-opportunity-workspace-drawer")).not.toBeInTheDocument();
  });

  it("shows the passed item immediately and the work panel", () => {
    render(<SalesOpportunityWorkspaceDrawer item={item()} open onClose={vi.fn()} onStageChanged={vi.fn()} />);

    expect(screen.getByText("uach.cl")).toBeInTheDocument();
    expect(screen.getByText("Centrífuga refrigerada")).toBeInTheDocument();
    expect(screen.getByTestId("work-panel-stub")).toBeInTheDocument();
  });

  it("closes on Escape and on the close button", () => {
    const onClose = vi.fn();
    render(<SalesOpportunityWorkspaceDrawer item={item()} open onClose={onClose} onStageChanged={vi.fn()} />);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("Cerrar"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("moves focus to the close button on open", async () => {
    render(<SalesOpportunityWorkspaceDrawer item={item()} open onClose={vi.fn()} onStageChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Cerrar")).toHaveFocus());
  });

  it("returns focus to the previously focused element on close", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "Abrir oportunidad";
    document.body.appendChild(trigger);
    trigger.focus();
    expect(trigger).toHaveFocus();

    const onClose = vi.fn();
    const { rerender } = render(
      <SalesOpportunityWorkspaceDrawer item={item()} open onClose={onClose} onStageChanged={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByText("Cerrar")).toHaveFocus());

    rerender(<SalesOpportunityWorkspaceDrawer item={item()} open={false} onClose={onClose} onStageChanged={vi.fn()} />);

    await waitFor(() => expect(trigger).toHaveFocus());

    document.body.removeChild(trigger);
  });

  it("changes stage and notifies the parent on success", async () => {
    vi.mocked(transitionSalesOpportunityStage).mockResolvedValue({
      ...item(),
      stage: "quoting",
      version: 3,
      updated_at: "2026-08-28T12:00:00+00:00",
    });

    const onStageChanged = vi.fn();
    render(<SalesOpportunityWorkspaceDrawer item={item()} open onClose={vi.fn()} onStageChanged={onStageChanged} />);

    fireEvent.change(screen.getByLabelText("Cambiar etapa"), { target: { value: "quoting" } });

    await waitFor(() => expect(onStageChanged).toHaveBeenCalled());
    expect(vi.mocked(transitionSalesOpportunityStage)).toHaveBeenCalledWith("sales_1", {
      stage: "quoting",
      expected_version: 2,
    });
  });

  it("reverts and shows the conflict message on 409", async () => {
    vi.mocked(transitionSalesOpportunityStage).mockRejectedValue(new OperatorApiError("conflict", 409));

    render(<SalesOpportunityWorkspaceDrawer item={item()} open onClose={vi.fn()} onStageChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Cambiar etapa"), { target: { value: "quoting" } });

    await waitFor(() =>
      expect(
        screen.getByText("Esta oportunidad cambió en otra sesión. Actualizamos el estado con la versión más reciente."),
      ).toBeInTheDocument(),
    );
    expect((screen.getByLabelText("Cambiar etapa") as HTMLSelectElement).value).toBe("qualifying");
  });

  it("keeps the drawer mounted briefly while it exits, for the close transition", () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <SalesOpportunityWorkspaceDrawer item={item()} open onClose={vi.fn()} onStageChanged={vi.fn()} />,
    );

    act(() => {
      rerender(<SalesOpportunityWorkspaceDrawer item={item()} open={false} onClose={vi.fn()} onStageChanged={vi.fn()} />);
    });
    expect(screen.getByTestId("sales-opportunity-workspace-drawer")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByTestId("sales-opportunity-workspace-drawer")).not.toBeInTheDocument();

    vi.useRealTimers();
  });
});
