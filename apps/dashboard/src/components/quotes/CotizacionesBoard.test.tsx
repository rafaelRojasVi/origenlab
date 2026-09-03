import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
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
  it("renders the five Cotizaciones lanes with the exact Spanish labels (Preparación removed, Cerrada added)", () => {
    render(
      <CotizacionesBoard
        queue={queue()}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Pendientes Drive" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Preparación" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Revisión" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Aprobada / por enviar" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Enviada / seguimiento" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cerrada" })).toBeInTheDocument();
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
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const reviewColumn = screen.getByTestId("cotizaciones-column-drop-review");
    expect(reviewColumn).toHaveTextContent(item.quote.quote_number);
  });

  it("groups draft, adjustments_requested, and pending_approval quotes all into the same review column", () => {
    const base = globalQuoteItemFixture();
    const items = (["draft", "adjustments_requested", "pending_approval"] as const).map(
      (revision_status, index) =>
        globalQuoteItemFixture({
          quote: {
            ...base.quote,
            quote_id: `quote_${index}${"a".repeat(31)}`,
            quote_number: `0118${index}-26`,
            revision_status,
            board_stage: "review",
          },
        }),
    );
    render(
      <CotizacionesBoard
        queue={queue({ items })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const reviewColumn = screen.getByTestId("cotizaciones-column-drop-review");
    for (const item of items) {
      expect(reviewColumn).toHaveTextContent(item.quote.quote_number);
    }
  });

  it("shows each Revisión sub-state as a distinct visible badge in the review column, without opening the drawer", () => {
    const base = globalQuoteItemFixture();
    const items = (["draft", "adjustments_requested", "pending_approval"] as const).map(
      (revision_status, index) =>
        globalQuoteItemFixture({
          quote: {
            ...base.quote,
            quote_id: `quote_${index}${"a".repeat(31)}`,
            quote_number: `0118${index}-26`,
            revision_status,
            board_stage: "review",
          },
        }),
    );
    render(
      <CotizacionesBoard
        queue={queue({ items })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const reviewColumn = screen.getByTestId("cotizaciones-column-drop-review");
    for (const label of ["Borrador", "Ajustes solicitados", "Lista para aprobación"]) {
      expect(within(reviewColumn).getByText(label)).toBeTruthy();
    }
  });

  it("shows document_number as the prominent identifier on a card, with quote_number secondary", () => {
    const item = globalQuoteItemFixture();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const card = screen.getByText(item.quote.document_number).closest("article");
    expect(card).not.toBeNull();
    expect(within(card!).getByText(item.quote.quote_number)).toBeInTheDocument();
  });

  it("labels a folder_ready drive workspace distinctly from ready and failed", () => {
    const base = globalQuoteItemFixture();
    const item = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: {
          ...base.quote.drive_workspace,
          provisioning_status: "folder_ready",
          sheet_file_id: null,
          sheet_web_url: null,
        },
      },
    });
    render(
      <CotizacionesBoard
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByText("Carpeta lista")).toBeInTheDocument();
  });

  it("shows Drive-only items in the Pendientes Drive lane", () => {
    const driveItem = drivePendingQuoteItemFixture();
    render(
      <CotizacionesBoard
        queue={queue({ driveItems: [driveItem] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
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
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const intakeColumn = screen.getByTestId("cotizaciones-column-drop-drive_intake");
    const card = intakeColumn.querySelector("article");
    expect(card).not.toHaveAttribute("draggable", "true");
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
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-approved_to_send"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).toHaveBeenCalledWith(item, "approve");
  });

  it("dropping a card onto its own review column is a no-op (submit_for_review/request_adjustments are never drag-triggerable)", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestConfirmSend = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={onRequestConfirmSend}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-review"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
    expect(onRequestConfirmSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("ya está en esta etapa");
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
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-sent_follow_up"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/no est.*permitido/i);
  });

  it("refuses dropping a durable card into the Drive intake lane", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
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
      quote: { ...globalQuoteItemFixture().quote, board_stage: "review" },
    });
    const dispatchWorkflowCommand = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand, pendingQuoteId: item.quote.quote_id })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-approved_to_send"), {
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
        onRequestConfirmSend={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("cambió en otra sesión");
    fireEvent.click(screen.getByText("Cerrar"));
    expect(dismissActionError).toHaveBeenCalled();
  });

  it("groups a closed quote into the Cerrada column and shows its outcome", () => {
    const item = globalQuoteItemFixture({
      quote: {
        ...globalQuoteItemFixture().quote,
        revision_status: "closed_won",
        board_stage: "closed",
        quote_outcome: "won",
      },
    });
    render(
      <CotizacionesBoard
        queue={queue({ items: [item] })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={vi.fn()}
      />,
    );

    const closedColumn = screen.getByTestId("cotizaciones-column-drop-closed");
    expect(closedColumn).toHaveTextContent(item.quote.quote_number);
    expect(closedColumn).toHaveTextContent("Cerrada · Ganada");
  });

  it("refuses dropping a sent quote directly into the Cerrada column -- closing is never drag-triggered", () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    const dispatchWorkflowCommand = vi.fn();
    const onRequestConfirmSend = vi.fn();
    render(
      <CotizacionesBoard
        queue={queue({ items: [item], dispatchWorkflowCommand })}
        onOpenQuote={vi.fn()}
        onAdoptDriveFolder={vi.fn()}
        onRequestConfirmSend={onRequestConfirmSend}
      />,
    );

    fireEvent.drop(screen.getByTestId("cotizaciones-column-drop-closed"), {
      dataTransfer: dataTransferFor(item.quote.quote_id),
    });

    expect(dispatchWorkflowCommand).not.toHaveBeenCalled();
    expect(onRequestConfirmSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
