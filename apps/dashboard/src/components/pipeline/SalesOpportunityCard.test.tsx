import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SalesOpportunityCard } from "./SalesOpportunityCard";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

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
    open_task_count: 1,
    next_task_id: "task_1",
    next_task_title: "Llamar a cliente",
    next_task_due_at: "2026-08-29T12:00:00+00:00",
    ...overrides,
  };
}

describe("SalesOpportunityCard", () => {
  it("shows the organization, title, owner, and next task", () => {
    render(
      <SalesOpportunityCard item={item()} onOpen={vi.fn()} onStageChange={vi.fn()} stagePending={false} />,
    );

    expect(screen.getByText("uach.cl")).toBeInTheDocument();
    expect(screen.getByText("Centrífuga refrigerada")).toBeInTheDocument();
    expect(screen.getByText("tatiana@origenlab.cl")).toBeInTheDocument();
    expect(screen.getByText("Llamar a cliente")).toBeInTheDocument();
  });

  it("shows a quiet empty state when there is no next task", () => {
    render(
      <SalesOpportunityCard
        item={item({ next_task_id: null, next_task_title: null, next_task_due_at: null, open_task_count: 0 })}
        onOpen={vi.fn()}
        onStageChange={vi.fn()}
        stagePending={false}
      />,
    );

    expect(screen.getByText("Sin próxima acción")).toBeInTheDocument();
  });

  it("falls back to the contact email when no account domain is known", () => {
    render(
      <SalesOpportunityCard
        item={item({ account_display_domain: null })}
        onOpen={vi.fn()}
        onStageChange={vi.fn()}
        stagePending={false}
      />,
    );

    expect(screen.getByText("buyer@example.cl")).toBeInTheDocument();
  });

  it("prefers the resolved durable organization name over the machine display domain", () => {
    render(
      <SalesOpportunityCard
        item={item({ organization_display_name: "Universidad Austral de Chile" })}
        onOpen={vi.fn()}
        onStageChange={vi.fn()}
        stagePending={false}
      />,
    );

    expect(screen.getByText("Universidad Austral de Chile")).toBeInTheDocument();
    expect(screen.queryByText("uach.cl")).not.toBeInTheDocument();
  });

  it("calls onOpen when the card body is clicked, not when the stage select is used", () => {
    const onOpen = vi.fn();
    const onStageChange = vi.fn();

    render(<SalesOpportunityCard item={item()} onOpen={onOpen} onStageChange={onStageChange} stagePending={false} />);

    fireEvent.change(screen.getByLabelText("Cambiar etapa"), { target: { value: "quoting" } });
    expect(onStageChange).toHaveBeenCalledWith("quoting");
    expect(onOpen).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Centrífuga refrigerada"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("does not call onOpen when Enter is pressed on the stage select", () => {
    const onOpen = vi.fn();
    const onStageChange = vi.fn();

    render(<SalesOpportunityCard item={item()} onOpen={onOpen} onStageChange={onStageChange} stagePending={false} />);

    const select = screen.getByLabelText("Cambiar etapa");
    fireEvent.keyDown(select, { key: "Enter" });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("shows a terminal badge with no stage select for a won opportunity", () => {
    render(
      <SalesOpportunityCard item={item({ stage: "won" })} onOpen={vi.fn()} onStageChange={vi.fn()} stagePending={false} />,
    );

    expect(screen.queryByLabelText("Cambiar etapa")).not.toBeInTheDocument();
    expect(screen.getByText("Ganada · cerrada")).toBeInTheDocument();
  });

  it("is draggable and reports its own id on dragstart", () => {
    render(<SalesOpportunityCard item={item()} onOpen={vi.fn()} onStageChange={vi.fn()} stagePending={false} />);

    const card = screen.getByRole("button", { name: /Centrífuga refrigerada/ });
    expect(card).toHaveAttribute("draggable", "true");

    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(card, { dataTransfer });
    expect(dataTransfer.setData).toHaveBeenCalledWith("text/plain", "sales_1");
  });
});
