import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CotizacionesBoard } from "./CotizacionesBoard";
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

function dataTransferFor(quoteId: string) {
  return { getData: () => quoteId, setData: vi.fn() };
}

describe("CotizacionesBoard", () => {
  it("renders the five Cotizaciones lanes with the exact Spanish labels", () => {
    render(
      <CotizacionesBoard
        queue={queue()}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Pendientes Drive" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Preparación" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Revisión" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Aprobada / por enviar" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Enviada / seguimiento" })).toBeInTheDocument();
  });

  it("groups a durable quote into the column matching its board_stage", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    render(
      <CotizacionesBoard
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const reviewColumn = screen.getByTestId("cotizaciones-column-drop-review");
    expect(reviewColumn).toHaveTextContent(item.quote.quote_number);
  });

  it("shows Drive-only items in the Pendientes Drive lane", () => {
    const driveItem = drivePendingQuoteItemFixture();
    render(
      <CotizacionesBoard
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const intakeColumn = screen.getByTestId("cotizaciones-column-drop-drive_intake");
    expect(intakeColumn).toHaveTextContent(driveItem.document_identifier!);
    expect(intakeColumn).toHaveTextContent("Sin registro CRM");
  });

  it("clicking a durable quote card calls onOpenQuote with that item", () => {
    const item = globalQuoteItemFixture();
    const onOpenQuote = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item] })}
        onOpenQuote={onOpenQuote}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText(item.quote.quote_number));
    expect(onOpenQuote).toHaveBeenCalledWith(item);
  });

  it('clicking "Incorporar al CRM" on a Drive-only card calls onAdoptDriveFolder', () => {
    const driveItem = drivePendingQuoteItemFixture();
    const onAdoptDriveFolder = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={onAdoptDriveFolder}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));
    expect(onAdoptDriveFolder).toHaveBeenCalledWith(driveItem);
  });

  it("a Drive-only card is never draggable", () => {
    const driveItem = drivePendingQuoteItemFixture();
    render(
      <CotizacionesBoard
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const intakeColumn = screen.getByTestId("cotizaciones-column-drop-drive_intake");
    const card = intakeColumn.querySelector("article");
    expect(card).not.toHaveAttribute("draggable", "true");
  });

  it("dropping preparation -> review dispatches submit_for_review directly", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "preparation" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestAdjustments = vi.fn();
    const onRequestConfirmSend = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={onRequestAdjustments}
        onRequestConfirmSend={onRequestConfirmSend}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-review"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).toHaveBeenCalledWith(item, "submit_for_review");
    expect(onRequestAdjustments).not.toHaveBeenCalled();
    expect(onRequestConfirmSend).not.toHaveBeenCalled();
  });

  it("dropping review -> approved_to_send dispatches approve directly", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-approved_to_send"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).toHaveBeenCalledWith(item, "approve");
  });

  it("dropping review -> preparation opens the adjustments confirmation instead of dispatching", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestAdjustments = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={onRequestAdjustments}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-preparation"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(onRequestAdjustments).toHaveBeenCalledWith(item);
    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("dropping approved_to_send -> sent_follow_up opens the send confirmation instead of dispatching", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "approved_to_send" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestConfirmSend = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={onRequestConfirmSend}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-sent_follow_up"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(onRequestConfirmSend).toHaveBeenCalledWith(item);
    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("refuses a skip-stage drop and never dispatches", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "preparation" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-approved_to_send"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/no est.*permitido/i);
  });

  it("refuses dropping a durable card into the Drive intake lane", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "preparation" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-drive_intake"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("ignores any drop while a workflow command is already pending", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "preparation" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand, pendingQuoteId: item.quote.quote_id })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-review"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("shows a dismissible action-conflict banner from the queue", () => {
    const dismissActionError = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ actionError: "Esta cotización cambió en otra sesión.", dismissActionError })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestAdjustments={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("cambió en otra sesión");
    fireEvent.click(screen.getByText("Cerrar"));
    expect(dismissActionError).toHaveBeenCalled();
  });
});
