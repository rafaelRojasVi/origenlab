import "@testing-library/jest-dom";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QuoteDetailDrawer } from "./QuoteDetailDrawer";
import * as client from "../../api/customerQuoteClient";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

vi.mock("../../api/customerQuoteClient");

const EMPTY_EVENTS = { meta: { count: 0 }, items: [] };

function drawerProps(overrides: Record<string, unknown> = {}) {
  return {
    onClose: vi.fn(),
    onOpenVentas: vi.fn(),
    onDispatchWorkflowCommand: vi.fn().mockResolvedValue(undefined),
    onRequestAdjustments: vi.fn(),
    onRequestConfirmSend: vi.fn(),
    onRequestClose: vi.fn(),
    dispatchPending: false,
    ...overrides,
  };
}

describe("QuoteDetailDrawer", () => {
  beforeEach(() => {
    vi.mocked(client.fetchCustomerQuoteEvents).mockReset().mockResolvedValue(EMPTY_EVENTS);
  });

  it("refreshes the quote on open and shows identity, opportunity and Drive links", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);

    await waitFor(() => expect(client.fetchCustomerQuote).toHaveBeenCalledWith(fixture.quote.quote_id));
    screen.getByText("CEAF");
    screen.getByText("01183-26");
    screen.getByText("CN01183");
    screen.getByText("Centrífuga CEAF");
    expect(screen.getByRole("link", { name: /Abrir carpeta/ })).toHaveAttribute(
      "href",
      "https://drive.google.com/drive/folders/f1",
    );
  });

  it("shows document_number as the drawer heading and quote_number as the subtitle", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);

    await waitFor(() => expect(client.fetchCustomerQuote).toHaveBeenCalledWith(fixture.quote.quote_id));

    expect(
      screen.getByRole("heading", { name: fixture.quote.document_number }),
    ).toBeInTheDocument();
    expect(screen.getByText(fixture.quote.quote_number)).toBeInTheDocument();
  });

  it("shows the failure category and a retry action for a failed workspace, reusing the retry command", async () => {
    const base = globalQuoteItemFixture();
    const failed = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: {
          ...base.quote.drive_workspace,
          provisioning_status: "failed",
          failure_category: "drive_unavailable",
          retryable: true,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: failed.quote });
    vi.mocked(client.retryCustomerQuoteDriveWorkspace).mockResolvedValue({
      ...failed.quote,
      drive_workspace: { ...failed.quote.drive_workspace, provisioning_status: "ready" },
    });

    render(<QuoteDetailDrawer item={failed} open {...drawerProps()} />);
    await waitFor(() => screen.getByText(/Google Drive no está disponible/));

    fireEvent.click(screen.getByRole("button", { name: /Reintentar/ }));
    await waitFor(() =>
      expect(client.retryCustomerQuoteDriveWorkspace).toHaveBeenCalledWith(failed.quote.quote_id, {
        expected_version: failed.quote.drive_workspace.version,
      }),
    );
  });

  it("renders a pending workspace as provisioning language, never as a failure", async () => {
    const base = globalQuoteItemFixture();
    const pending = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: {
          ...base.quote.drive_workspace,
          provisioning_status: "pending",
          retryable: false,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: pending.quote });

    render(<QuoteDetailDrawer item={pending} open {...drawerProps()} />);
    await waitFor(() => screen.getByText(/Preparando carpeta en Drive/));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/no se pudo crear/)).toBeNull();
  });

  it("never renders a raw Drive id as a link — only server-validated https URLs", async () => {
    const base = globalQuoteItemFixture();
    const noLinks = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: { ...base.quote.drive_workspace, folder_web_url: null, sheet_web_url: null },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: noLinks.quote });

    render(<QuoteDetailDrawer item={noLinks} open {...drawerProps()} />);
    await waitFor(() => screen.getByText("01183-26"));
    expect(screen.queryByRole("link", { name: /Abrir carpeta/ })).toBeNull();
  });

  it("'Ver en Ventas' calls onOpenVentas with the exact durable opportunity id", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onOpenVentas = vi.fn();

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps({ onOpenVentas })} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: "Ver en Ventas" }));
    expect(onOpenVentas).toHaveBeenCalledWith(fixture.quote.sales_opportunity_id);
    expect(onOpenVentas).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when item is null", () => {
    render(<QuoteDetailDrawer item={null} open={false} {...drawerProps()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("QuoteDetailDrawer workflow state (CRM-Q2)", () => {
  beforeEach(() => {
    vi.mocked(client.fetchCustomerQuoteEvents).mockReset().mockResolvedValue(EMPTY_EVENTS);
  });

  it("shows the current revision status", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "pending_approval", board_stage: "review" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);
    await waitFor(() => screen.getByText("01183-26"));

    expect(screen.getByText(/Lista para aprobación/)).toBeInTheDocument();
  });

  it("fetches and shows the event history", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    vi.mocked(client.fetchCustomerQuoteEvents).mockResolvedValue({
      meta: { count: 1 },
      items: [
        {
          event_id: "event_1",
          event_type: "quote_submitted_for_review",
          actor_key: "tatiana@origenlab.cl",
          payload: { revision_number: 1, from_status: "draft", to_status: "pending_approval" },
          created_at: "2026-09-02T12:00:00+00:00",
        },
      ],
    });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);

    await waitFor(() => expect(client.fetchCustomerQuoteEvents).toHaveBeenCalledWith(fixture.quote.quote_id));
    await waitFor(() => screen.getByText("Enviada a revisión"));
  });

  it("shows the legal next action for the current stage and dispatches it directly", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "draft", board_stage: "review" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onDispatchWorkflowCommand = vi.fn().mockResolvedValue(undefined);

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps({ onDispatchWorkflowCommand })} />);
    await waitFor(() => screen.getByRole("button", { name: "Enviar a aprobación" }));

    fireEvent.click(screen.getByRole("button", { name: "Enviar a aprobación" }));

    expect(onDispatchWorkflowCommand).toHaveBeenCalledWith(
      expect.objectContaining({ quote: expect.objectContaining({ quote_id: fixture.quote.quote_id }) }),
      "submit_for_review",
    );
  });

  it("routes request_adjustments through onRequestAdjustments instead of dispatching", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "pending_approval", board_stage: "review" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onDispatchWorkflowCommand = vi.fn().mockResolvedValue(undefined);
    const onRequestAdjustments = vi.fn();

    render(
      <QuoteDetailDrawer
        item={fixture}
        open
        {...drawerProps({ onDispatchWorkflowCommand, onRequestAdjustments })}
      />,
    );
    await waitFor(() => screen.getByRole("button", { name: "Solicitar ajustes" }));

    fireEvent.click(screen.getByRole("button", { name: "Solicitar ajustes" }));

    expect(onRequestAdjustments).toHaveBeenCalled();
    expect(onDispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("routes confirm_send through onRequestConfirmSend instead of dispatching", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "approved", board_stage: "approved_to_send" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onDispatchWorkflowCommand = vi.fn().mockResolvedValue(undefined);
    const onRequestConfirmSend = vi.fn();

    render(
      <QuoteDetailDrawer
        item={fixture}
        open
        {...drawerProps({ onDispatchWorkflowCommand, onRequestConfirmSend })}
      />,
    );
    await waitFor(() => screen.getByRole("button", { name: "Confirmar envío" }));

    fireEvent.click(screen.getByRole("button", { name: "Confirmar envío" }));

    expect(onRequestConfirmSend).toHaveBeenCalled();
    expect(onDispatchWorkflowCommand).not.toHaveBeenCalled();
  });

  it("shows no submit/approve/adjust/send actions for a sent revision -- only Cerrar cotización", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);
    await waitFor(() => screen.getByText(/Enviada/));

    expect(screen.queryByRole("button", { name: "Confirmar envío" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cerrar cotización" })).toBeInTheDocument();
  });

  it("routes Cerrar cotización through onRequestClose for a sent revision", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onRequestClose = vi.fn();

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps({ onRequestClose })} />);
    await waitFor(() => screen.getByRole("button", { name: "Cerrar cotización" }));

    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));

    expect(onRequestClose).toHaveBeenCalled();
  });

  it("does not show Cerrar cotización for a non-sent revision (e.g. approved)", async () => {
    const fixture = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "approved", board_stage: "approved_to_send" },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);
    await waitFor(() => screen.getByRole("button", { name: "Confirmar envío" }));

    expect(screen.queryByRole("button", { name: "Cerrar cotización" })).toBeNull();
  });

  it("shows the Ganada outcome for a closed_won revision, with no further action buttons", async () => {
    const fixture = globalQuoteItemFixture({
      quote: {
        ...globalQuoteItemFixture().quote,
        revision_status: "closed_won",
        board_stage: "closed",
        quote_outcome: "won",
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open {...drawerProps()} />);
    await waitFor(() => screen.getByText(/Cerrada · Ganada/));

    expect(screen.queryByRole("button", { name: "Cerrar cotización" })).toBeNull();
  });
});
