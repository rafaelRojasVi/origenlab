import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SalesOpportunityBoard } from "./SalesOpportunityBoard";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";
import type { useSalesOpportunityBoard } from "./useSalesOpportunityBoard";

type Board = ReturnType<typeof useSalesOpportunityBoard>;

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

function board(overrides: Partial<Board> = {}): Board {
  return {
    items: [],
    loading: false,
    error: null,
    enabledToggles: [],
    toggleStage: vi.fn(),
    refetch: vi.fn(),
    changeStage: vi.fn(),
    pendingStageChangeId: null,
    stageError: null,
    dismissStageError: vi.fn(),
    ...overrides,
  } as Board;
}

describe("SalesOpportunityBoard", () => {
  it("shows a loading skeleton while the board loads", () => {
    render(<SalesOpportunityBoard board={board({ loading: true })} onOpenOpportunity={vi.fn()} />);
    expect(screen.getByRole("status", { name: "Cargando pipeline" })).toBeInTheDocument();
  });

  it("shows a retry banner on load error", () => {
    const refetch = vi.fn();
    render(<SalesOpportunityBoard board={board({ error: "network down", refetch })} onOpenOpportunity={vi.fn()} />);

    expect(screen.getByText("No pudimos cargar el pipeline.")).toBeInTheDocument();
    screen.getByText("Reintentar").click();
    expect(refetch).toHaveBeenCalled();
  });

  it("shows the empty-pipeline copy when there are no active or toggled opportunities", () => {
    render(<SalesOpportunityBoard board={board()} onOpenOpportunity={vi.fn()} />);

    expect(screen.getByText("No hay oportunidades activas todavía.")).toBeInTheDocument();
    expect(
      screen.getByText("Promueve oportunidades detectadas desde Negocios para comenzar a gestionarlas aquí."),
    ).toBeInTheDocument();
  });

  it("groups cards into the correct stage columns", () => {
    render(
      <SalesOpportunityBoard
        board={board({ items: [item(), item({ sales_opportunity_id: "sales_2", stage: "new", title: "Autoclave" })] })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    const newColumn = screen.getByRole("heading", { name: "Nueva" }).closest("div")!.parentElement!;
    expect(within(newColumn).getByText("Autoclave")).toBeInTheDocument();

    const qualifyingColumn = screen.getByRole("heading", { name: "Calificando" }).closest("div")!.parentElement!;
    expect(within(qualifyingColumn).getByText("Centrífuga refrigerada")).toBeInTheDocument();
  });

  it("calls onOpenOpportunity when a card is opened", () => {
    const onOpenOpportunity = vi.fn();
    render(<SalesOpportunityBoard board={board({ items: [item()] })} onOpenOpportunity={onOpenOpportunity} />);

    screen.getByText("Centrífuga refrigerada").click();
    expect(onOpenOpportunity).toHaveBeenCalledWith(item());
  });

  it("toggling Ganadas calls toggleStage and reflects pressed state", () => {
    const toggleStage = vi.fn();
    const { rerender } = render(
      <SalesOpportunityBoard board={board({ toggleStage })} onOpenOpportunity={vi.fn()} />,
    );

    const button = screen.getByRole("button", { name: "Ganadas" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    button.click();
    expect(toggleStage).toHaveBeenCalledWith("won");

    rerender(<SalesOpportunityBoard board={board({ toggleStage, enabledToggles: ["won"] })} onOpenOpportunity={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Ganadas" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows a dismissible stage-conflict banner", () => {
    const dismissStageError = vi.fn();
    render(
      <SalesOpportunityBoard
        board={board({ items: [item()], stageError: "Esta oportunidad cambió en otra sesión. Actualizamos el estado con la versión más reciente.", dismissStageError })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Esta oportunidad cambió en otra sesión");
    screen.getByText("Cerrar").click();
    expect(dismissStageError).toHaveBeenCalled();
  });

  it("dropping a card on a column calls changeStage with that column's stage", () => {
    const changeStage = vi.fn();
    render(<SalesOpportunityBoard board={board({ items: [item()], changeStage })} onOpenOpportunity={vi.fn()} />);

    const newColumnDropZone = screen.getByTestId("pipeline-column-drop-new");
    const dataTransfer = { getData: () => "sales_1", setData: vi.fn() };

    fireEvent.drop(newColumnDropZone, { dataTransfer });
    expect(changeStage).toHaveBeenCalledWith(item(), "new");
  });

  it("ignores a drop while a stage mutation is already pending", () => {
    const changeStage = vi.fn();
    render(
      <SalesOpportunityBoard
        board={board({ items: [item()], changeStage, pendingStageChangeId: "sales_1" })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    const newColumnDropZone = screen.getByTestId("pipeline-column-drop-new");
    const dataTransfer = { getData: () => "sales_1", setData: vi.fn() };

    fireEvent.drop(newColumnDropZone, { dataTransfer });
    expect(changeStage).not.toHaveBeenCalled();
  });

  it("disables every card's stage control while any stage mutation is pending", () => {
    render(
      <SalesOpportunityBoard
        board={board({
          items: [item(), item({ sales_opportunity_id: "sales_2", stage: "new", title: "Autoclave" })],
          pendingStageChangeId: "sales_1",
        })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    for (const select of screen.getAllByLabelText("Cambiar etapa")) {
      expect(select).toBeDisabled();
    }
  });
});
