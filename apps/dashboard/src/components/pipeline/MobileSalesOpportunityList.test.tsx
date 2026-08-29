import "@testing-library/jest-dom";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MobileSalesOpportunityList } from "./MobileSalesOpportunityList";
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
    stage: "new",
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

describe("MobileSalesOpportunityList", () => {
  it("defaults to the Nueva stage and shows only its cards", () => {
    render(
      <MobileSalesOpportunityList
        board={board({ items: [item(), item({ sales_opportunity_id: "sales_2", stage: "qualifying", title: "Autoclave" })] })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    expect(screen.getByText("Centrífuga refrigerada")).toBeInTheDocument();
    expect(screen.queryByText("Autoclave")).not.toBeInTheDocument();
  });

  it("switching to another already-loaded active stage shows its cards without toggling", () => {
    const toggleStage = vi.fn();
    render(
      <MobileSalesOpportunityList
        board={board({
          toggleStage,
          items: [item(), item({ sales_opportunity_id: "sales_2", stage: "qualifying", title: "Autoclave" })],
        })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    act(() => {
      screen.getByRole("tab", { name: "Calificando" }).click();
    });

    expect(screen.getByText("Autoclave")).toBeInTheDocument();
    expect(toggleStage).not.toHaveBeenCalled();
  });

  it("selecting a not-yet-enabled toggle stage calls toggleStage once", () => {
    const toggleStage = vi.fn();
    render(<MobileSalesOpportunityList board={board({ toggleStage })} onOpenOpportunity={vi.fn()} />);

    act(() => {
      screen.getByRole("tab", { name: "Ganadas" }).click();
    });
    expect(toggleStage).toHaveBeenCalledWith("won");
  });

  it("selecting an already-enabled toggle stage does not toggle it again", () => {
    const toggleStage = vi.fn();
    render(
      <MobileSalesOpportunityList
        board={board({ toggleStage, enabledToggles: ["won"], items: [item({ stage: "won" })] })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    act(() => {
      screen.getByRole("tab", { name: "Ganadas" }).click();
    });
    expect(toggleStage).not.toHaveBeenCalled();
    expect(screen.getByText("Centrífuga refrigerada")).toBeInTheDocument();
  });

  it("shows a per-stage empty message distinct from the whole-pipeline empty state", () => {
    render(
      <MobileSalesOpportunityList
        board={board({ enabledToggles: ["dormant"], items: [item({ stage: "new" })] })}
        onOpenOpportunity={vi.fn()}
      />,
    );

    act(() => {
      screen.getByRole("tab", { name: "Dormidas" }).click();
    });
    expect(screen.getByText("Sin oportunidades en esta etapa.")).toBeInTheDocument();
  });

  it("shows the whole-pipeline empty state when there is nothing anywhere", () => {
    render(<MobileSalesOpportunityList board={board()} onOpenOpportunity={vi.fn()} />);
    expect(screen.getByText("No hay oportunidades activas todavía.")).toBeInTheDocument();
  });

  it("calls onOpenOpportunity from the visible card", () => {
    const onOpenOpportunity = vi.fn();
    render(<MobileSalesOpportunityList board={board({ items: [item()] })} onOpenOpportunity={onOpenOpportunity} />);

    act(() => {
      screen.getByText("Centrífuga refrigerada").click();
    });
    expect(onOpenOpportunity).toHaveBeenCalledWith(item());
  });
});
