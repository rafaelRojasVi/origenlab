import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CotizacionesMobileList } from "./CotizacionesMobileList";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../../test/fixtures/customerQuoteFixtures";
import type { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";

type Queue = ReturnType<typeof useCustomerQuotesGlobal>;

function queue(overrides: Partial<Queue> = {}): Queue {
  return {
    items: [],
    driveItems: [],
    rows: [],
    isEmpty: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
    stageToggles: [],
    toggleStage: vi.fn(),
    driveStatusToggles: [],
    toggleDriveStatus: vi.fn(),
    dispatchWorkflowCommand: vi.fn(),
    pendingQuoteId: null,
    actionError: null,
    dismissActionError: vi.fn(),
    ...overrides,
  } as Queue;
}

describe("CotizacionesMobileList", () => {
  it("renders grouped sections for every lane with a count", () => {
    render(
      <CotizacionesMobileList
        queue={queue()}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Pendientes Drive" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Preparación" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Revisión" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Aprobada / por enviar" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Enviada / seguimiento" })).toBeInTheDocument();
  });

  it("groups a durable quote under its board_stage section", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "approved_to_send" },
    });
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const section = screen.getByTestId("cotizaciones-mobile-section-approved_to_send");
    expect(within(section).getByText(item.quote.quote_number)).toBeInTheDocument();
  });

  it("shows Drive-only items under the Pendientes Drive section", () => {
    const driveItem = drivePendingQuoteItemFixture();
    render(
      <CotizacionesMobileList
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const section = screen.getByTestId("cotizaciones-mobile-section-drive_intake");
    expect(within(section).getByText(driveItem.document_identifier!)).toBeInTheDocument();
  });

  it("clicking a durable card calls onOpenQuote", () => {
    const item = globalQuoteItemFixture();
    const onOpenQuote = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item] })}
        onOpenQuote={onOpenQuote}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText(item.quote.quote_number));
    expect(onOpenQuote).toHaveBeenCalledWith(item);
  });

  it('clicking "Incorporar al CRM" calls onAdoptDriveFolder', () => {
    const driveItem = drivePendingQuoteItemFixture();
    const onAdoptDriveFolder = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={onAdoptDriveFolder}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));
    expect(onAdoptDriveFolder).toHaveBeenCalledWith(driveItem);
  });

  it("clicking a non-confirmation stage action dispatches directly", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "draft", board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Enviar a aprobación" }));
    expect(dispatchWorkflowCommand).toHaveBeenCalledWith(item, "submit_for_review");
  });

  it("clicking request_adjustments opens the confirmation callback instead of dispatching", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "pending_approval", board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestAdjustments = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={onRequestAdjustments}
        onRequestConfirmSend={vi.fn()}
        onRequestClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Solicitar ajustes" }));
    expect(onRequestAdjustments).toHaveBeenCalledWith(item);
    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("clicking confirm_send opens the confirmation callback instead of dispatching", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "approved", board_stage: "approved_to_send" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestConfirmSend = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={onRequestConfirmSend}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirmar envío" }));
    expect(onRequestConfirmSend).toHaveBeenCalledWith(item);
    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("sent_follow_up cards offer no submit/approve/adjust/send actions -- only Cerrar cotización (CRM-Q2B)", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const section = screen.getByTestId("cotizaciones-mobile-section-sent_follow_up");
    // The card itself carries role="button" (opens the drawer) -- assert on
    // native <button> elements (the workflow-action buttons) specifically.
    const buttons = section.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("Cerrar cotización");
  });

  it("clicking Cerrar cotización on a sent card calls onRequestClose", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    const onRequestClose = vi.fn();
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={onRequestClose}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));
    expect(onRequestClose).toHaveBeenCalledWith(item);
  });

  it("closed cards (closed_won) show no further action buttons", () => {
    const item = globalQuoteItemFixture({
      quote: {
        ...globalQuoteItemFixture().quote,
        revision_status: "closed_won",
        board_stage: "closed",
        quote_outcome: "won",
      },
    });
    render(
      <CotizacionesMobileList
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestClose={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const section = screen.getByTestId("cotizaciones-mobile-section-closed");
    expect(section.querySelectorAll("button")).toHaveLength(0);
  });
});
