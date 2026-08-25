import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CommercialWorkQueueResponse } from "../../api/commercialOperationsTypes";
import { CommercialWorkQueuePanel } from "./CommercialWorkQueuePanel";

vi.mock("./CommercialOpportunityDetailDrawer", () => ({
  CommercialOpportunityDetailDrawer: ({
    opportunityId,
    open,
  }: {
    opportunityId: string | null;
    open: boolean;
  }) =>
    open ? (
      <div role="dialog">
        Opportunity detail {opportunityId}
      </div>
    ) : null,
}));

function localIso(dayOffset: number, hour: number) {
  const now = new Date();

  return new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + dayOffset,
    hour,
    0,
    0,
  ).toISOString();
}

function task(
  taskId: string,
  opportunityId: string,
  title: string,
  dueAt: string | null,
) {
  return {
    task: {
      task_id: taskId,
      opportunity_id: opportunityId,
      account_id: null,
      contact_id: null,
      title,
      status: "open" as const,
      priority: "normal" as const,
      due_at: dueAt,
      owner_key: "tatiana",
      version: 1,
      created_by: "tatiana@origenlab.cl",
      updated_by: "tatiana@origenlab.cl",
      completed_at: null,
      created_at: localIso(0, 8),
      updated_at: localIso(0, 8),
    },
    contact_display_email: "buyer@example.cl",
    account_display_domain: "example.cl",
    canonical_stage: "quote_sent",
    machine_review_status: "needs_review",
  };
}

function opportunity(opportunityId: string) {
  return {
    opportunity_id: opportunityId,
    contact_display_email: "buyer@example.cl",
    account_display_domain: "example.cl",
    canonical_stage: "quote_sent",
    machine_review_status: "needs_review",
    confirmation_status: null,
    manual_stage: null,
    owner_key: "tatiana",
    operator_state_version: null,
  };
}

describe("CommercialWorkQueuePanel", () => {
  it("renders prioritized durable work instead of duplicate KPI cards", () => {
    const queue: CommercialWorkQueueResponse = {
      open_tasks: [
        task(
          "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "Llamar por cotización",
          localIso(-1, 16),
        ),
        task(
          "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "o_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "Confirmar recepción",
          localIso(0, 16),
        ),
      ],
      review_opportunities: [
        opportunity("o_cccccccccccccccccccccccccccccccc"),
      ],
      quote_followups: [
        opportunity("o_dddddddddddddddddddddddddddddddd"),
      ],
    };

    render(
      <CommercialWorkQueuePanel
        queue={queue}
        onSelectContact={() => {}}
      />,
    );

    screen.getByRole("heading", {
      name: "Prioridad comercial",
    });

    screen.getByText("Llamar por cotización");
    screen.getByText("Confirmar recepción");

    expect(
      screen.getByRole("heading", {
        name: "Seguimientos vencidos",
      }),
    ).toBeTruthy();

    expect(
      screen.getByRole("heading", {
        name: "Revisión humana",
      }),
    ).toBeTruthy();

    expect(
      screen.queryByText("Tareas abiertas"),
    ).toBeNull();
  });

  it("opens the existing opportunity detail surface", () => {
    const opportunityId =
      "o_cccccccccccccccccccccccccccccccc";

    const queue: CommercialWorkQueueResponse = {
      open_tasks: [],
      review_opportunities: [
        opportunity(opportunityId),
      ],
      quote_followups: [],
    };

    render(
      <CommercialWorkQueuePanel
        queue={queue}
        onSelectContact={() => {}}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: `Abrir oportunidad ${opportunityId}`,
      }),
    );

    screen.getByRole("dialog");
    screen.getByText(
      `Opportunity detail ${opportunityId}`,
    );
  });

  it("renders useful empty states", () => {
    render(
      <CommercialWorkQueuePanel
        queue={{
          open_tasks: [],
          review_opportunities: [],
          quote_followups: [],
        }}
        onSelectContact={() => {}}
      />,
    );

    screen.getByText("No hay tareas vencidas.");
    screen.getByText(
      "No hay tareas con vencimiento hoy.",
    );
    screen.getByText(
      "No hay oportunidades pendientes de revisión.",
    );
    screen.getByText(
      "No hay cotizaciones pendientes de seguimiento.",
    );
  });
});
