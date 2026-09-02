import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CotizacionesPage } from "./CotizacionesPage";
import * as client from "../api/customerQuoteClient";
import * as opsClient from "../api/commercialOperationsClient";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../test/fixtures/customerQuoteFixtures";

vi.mock("../api/customerQuoteClient");
vi.mock("../api/commercialOperationsClient");

// The board and mobile-list child components are stubbed (not the
// useCustomerQuotesGlobal hook) so these page-level tests drive
// CotizacionesPage's own callback wiring -- open drawer / open adoption
// modal / open the two confirmation dialogs -- through simple buttons,
// while CotizacionesBoard.test.tsx / CotizacionesMobileList.test.tsx
// separately cover the real grouping/drag-drop rendering logic. This also
// sidesteps the two layouts both being mounted at once (desktop hidden via
// CSS, not unmounted) duplicating every text match, exactly like
// VentasPage.test.tsx's equivalent stubbing of SalesOpportunityBoard /
// MobileSalesOpportunityList.
vi.mock("../components/quotes/CotizacionesBoard", () => ({
  CotizacionesBoard: ({
    queue,
    onOpenQuote,
    onAdoptDriveFolder,
    onRequestConfirmSend,
  }: {
    queue: { items: unknown[]; driveItems: unknown[] };
    onOpenQuote: (item: unknown) => void;
    onAdoptDriveFolder: (item: unknown) => void;
    onRequestConfirmSend: (item: unknown) => void;
  }) => (
    <div data-testid="cotizaciones-board-desktop">
      <span data-testid="board-item-count">{queue.items.length}</span>
      <span data-testid="board-drive-item-count">{queue.driveItems.length}</span>
      {queue.items[0] ? <button onClick={() => onOpenQuote(queue.items[0])}>abrir-desktop</button> : null}
      {queue.driveItems[0] ? (
        <button onClick={() => onAdoptDriveFolder(queue.driveItems[0])}>adoptar-desktop</button>
      ) : null}
      {queue.items[0] ? (
        <button onClick={() => onRequestConfirmSend(queue.items[0])}>enviar-desktop</button>
      ) : null}
    </div>
  ),
}));

vi.mock("../components/quotes/CotizacionesMobileList", () => ({
  CotizacionesMobileList: () => <div data-testid="mobile-stub" />,
}));

vi.mock("../components/quotes/QuoteDetailDrawer", () => ({
  QuoteDetailDrawer: ({
    item,
    open,
    onClose,
    onOpenVentas,
    onRequestAdjustments,
    onRequestClose,
  }: {
    item: { quote: { quote_number: string; sales_opportunity_id: string } } | null;
    open: boolean;
    onClose: () => void;
    onOpenVentas: (opportunityId: string) => void;
    onRequestAdjustments: (item: { quote: { quote_number: string; sales_opportunity_id: string } }) => void;
    onRequestClose: (item: { quote: { quote_number: string; sales_opportunity_id: string } }) => void;
  }) =>
    open && item ? (
      <div role="dialog" data-testid="drawer-stub">
        <span>{item.quote.quote_number}</span>
        <button onClick={onClose}>cerrar-drawer</button>
        <button onClick={() => onOpenVentas(item.quote.sales_opportunity_id)}>Ver en Ventas</button>
        <button onClick={() => onRequestAdjustments(item)}>ajustes-drawer</button>
        <button onClick={() => onRequestClose(item)}>cerrar-cotizacion-drawer</button>
      </div>
    ) : null,
}));

vi.mock("../components/quotes/AdoptDriveFolderModal", () => ({
  AdoptDriveFolderModal: ({
    item,
    open,
    onClose,
    onAdopted,
  }: {
    item: unknown;
    open: boolean;
    onClose: () => void;
    onAdopted: (quote: unknown) => void;
  }) =>
    open && item ? (
      <div data-testid="adopt-drive-folder-modal">
        <button onClick={onClose}>cerrar-adopt</button>
        <button onClick={() => onAdopted(globalQuoteItemFixture().quote)}>confirmar-adopt</button>
      </div>
    ) : null,
}));

vi.mock("../components/quotes/WorkflowConfirmDialog", () => ({
  WorkflowConfirmDialog: ({
    open,
    item,
    title,
    onConfirm,
    onClose,
  }: {
    open: boolean;
    item: unknown;
    title: string;
    onConfirm: (item: unknown) => void;
    onClose: () => void;
  }) =>
    open && item ? (
      <div data-testid={`confirm-stub-${title}`}>
        <button onClick={onClose}>{`cerrar-${title}`}</button>
        <button onClick={() => onConfirm(item)}>{`confirmar-${title}`}</button>
      </div>
    ) : null,
}));

describe("CotizacionesPage", () => {
  beforeEach(() => {
    vi.mocked(opsClient.fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchCustomerQuotesGlobal).mockReset();
    vi.mocked(client.fetchDrivePendingQuotes).mockReset().mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });
    vi.mocked(client.fetchCustomerQuote).mockReset();
    vi.mocked(client.createCustomerQuote).mockReset();
    vi.mocked(opsClient.createManualSalesOpportunity).mockReset();
  });

  it("renders the durable global queue, not a placeholder", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("board-item-count")).toHaveTextContent("1"));
    expect(screen.queryByText("Aún no hay cotizaciones")).toBeNull();
  });

  it("shows an honest empty state when there are no quotes yet", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
    expect(screen.queryByTestId("cotizaciones-board-desktop")).toBeNull();
  });

  it("filters the visible rows by search text against quote/document number and customer", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("board-item-count")).toHaveTextContent("1"));

    const search = screen.getByRole("searchbox", { name: /buscar/i });
    fireEvent.change(search, { target: { value: "no-match" } });
    await waitFor(() => screen.getByText("Sin resultados para estos filtros"));
    expect(screen.queryByTestId("cotizaciones-board-desktop")).toBeNull();
  });

  it("renders both the desktop board and mobile list stubs (no report table)", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByTestId("cotizaciones-board-desktop"));
    expect(screen.getByTestId("mobile-stub")).toBeInTheDocument();
    expect(screen.queryAllByRole("table")).toHaveLength(0);
  });

  it("opens the detail drawer from the board's onOpenQuote callback", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("abrir-desktop"));

    fireEvent.click(screen.getByText("abrir-desktop"));
    await waitFor(() => screen.getByTestId("drawer-stub"));
    expect(screen.getByTestId("drawer-stub")).toHaveTextContent("01183-26");
  });

  it("'Ver en Ventas' from the drawer passes the exact opportunity id through", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [fixture],
    });
    const onOpenVentas = vi.fn();

    render(<CotizacionesPage onOpenVentas={onOpenVentas} />);
    await waitFor(() => screen.getByText("abrir-desktop"));
    fireEvent.click(screen.getByText("abrir-desktop"));
    await waitFor(() => screen.getByTestId("drawer-stub"));

    fireEvent.click(screen.getByText("Ver en Ventas"));
    expect(onOpenVentas).toHaveBeenCalledWith(fixture.quote.sales_opportunity_id);
  });

  it("Nueva Cotización opens the create dialog, which allocates nothing on open", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: "Nueva Cotización" }));
    await waitFor(() => screen.getByTestId("nueva-cotizacion-dialog"));
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(client.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("a successful create closes the dialog, opens the real created quote's drawer (no placeholder), and background-refetches the queue", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [
        {
          sales_opportunity_id: "sales_" + "c".repeat(32),
          source_kind: "manual",
          source_opportunity_id: "sales_" + "c".repeat(32),
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Reactor CEAF",
          stage: "quoting",
          owner_key: "op@origenlab.cl",
          version: 1,
          created_by: "op@origenlab.cl",
          updated_by: "op@origenlab.cl",
          created_at: "2026-08-30T10:00:00Z",
          updated_at: "2026-08-30T10:00:00Z",
          stage_updated_at: "2026-08-30T10:00:00Z",
          contact_display_email: null,
          account_display_domain: null,
          organization_display_name: "CEAF",
          contact_display_name: "Tatiana Rojas",
          contact_primary_email: "tatiana@ceaf.cl",
          open_task_count: 0,
          next_task_id: null,
          next_task_title: null,
          next_task_due_at: null,
        },
      ],
    });
    const createdQuote = { ...globalQuoteItemFixture().quote, quote_number: "01186-26", sales_opportunity_id: "sales_" + "c".repeat(32) };
    vi.mocked(client.createCustomerQuote).mockResolvedValue(createdQuote);

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: "Nueva Cotización" }));
    await waitFor(() => screen.getByText("Reactor CEAF"));
    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));
    fireEvent.click(screen.getByRole("button", { name: /Crear cotización/ }));

    await waitFor(() => {
      expect(screen.queryByTestId("nueva-cotizacion-dialog")).toBeNull();
    });
    await waitFor(() => screen.getByTestId("drawer-stub"));
    expect(screen.getByTestId("drawer-stub")).toHaveTextContent("01186-26");
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
  });

  it("shows the Drive Pendientes items instead of the empty state when the CRM list is empty", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 3 },
      items: [
        drivePendingQuoteItemFixture({ folder_id: "f1", folder_name: "CN01191-ICN Chile", document_identifier: "CN01191" }),
        drivePendingQuoteItemFixture({
          folder_id: "f2",
          folder_name: "CN01190-Prof. Dr. Juan Matos Lale – Universidad Autónoma- UP400St",
          document_identifier: "CN01190",
        }),
        drivePendingQuoteItemFixture({ folder_id: "f3", folder_name: "CN1185 — Gustavo Zúñiga - UP200St", document_identifier: "CN1185" }),
      ],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("board-drive-item-count")).toHaveTextContent("3"));
    expect(screen.queryByText("Aún no hay cotizaciones")).toBeNull();
  });

  it("the empty state only fires when both the CRM list and the Drive-pending list are empty", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
  });

  it("'Recargar cotizaciones' refreshes both the CRM list and the Drive-pending list", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: /recargar cotizaciones/i }));

    await waitFor(() => {
      expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
      expect(client.fetchDrivePendingQuotes).toHaveBeenCalledTimes(2);
    });
  });

  it('the board\'s onAdoptDriveFolder callback opens the adoption modal, never the quote drawer', async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [drivePendingQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("adoptar-desktop"));

    fireEvent.click(screen.getByText("adoptar-desktop"));

    await waitFor(() => screen.getByTestId("adopt-drive-folder-modal"));
    expect(screen.queryByTestId("drawer-stub")).toBeNull();
  });

  it("a successful adoption closes the modal and refetches the queue", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [drivePendingQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("adoptar-desktop"));
    fireEvent.click(screen.getByText("adoptar-desktop"));
    await waitFor(() => screen.getByTestId("adopt-drive-folder-modal"));

    fireEvent.click(screen.getByText("confirmar-adopt"));

    await waitFor(() => expect(screen.queryByTestId("adopt-drive-folder-modal")).toBeNull());
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
  });

  it("the drawer's onRequestAdjustments callback opens the adjustments confirmation (CRM-Q2B: no longer board-triggerable, request_adjustments starts/ends inside the single review lane)", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("abrir-desktop"));
    fireEvent.click(screen.getByText("abrir-desktop"));
    await waitFor(() => screen.getByTestId("drawer-stub"));

    fireEvent.click(screen.getByText("ajustes-drawer"));

    await waitFor(() => screen.getByTestId("confirm-stub-Solicitar ajustes"));
  });

  it("confirming the adjustments dialog dispatches request_adjustments via the queue", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.requestCustomerQuoteAdjustments).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("abrir-desktop"));
    fireEvent.click(screen.getByText("abrir-desktop"));
    await waitFor(() => screen.getByTestId("drawer-stub"));
    fireEvent.click(screen.getByText("ajustes-drawer"));
    await waitFor(() => screen.getByTestId("confirm-stub-Solicitar ajustes"));

    fireEvent.click(screen.getByText("confirmar-Solicitar ajustes"));

    await waitFor(() => expect(client.requestCustomerQuoteAdjustments).toHaveBeenCalledTimes(1));
  });

  it("the board's onRequestConfirmSend callback opens the send confirmation, not the drawer", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("enviar-desktop"));
    fireEvent.click(screen.getByText("enviar-desktop"));

    await waitFor(() => screen.getByTestId("confirm-stub-Confirmar envío"));
    expect(screen.queryByTestId("drawer-stub")).toBeNull();
  });

  it("confirming the send dialog dispatches confirm_send via the queue, never sends email itself", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.confirmCustomerQuoteSend).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("enviar-desktop"));
    fireEvent.click(screen.getByText("enviar-desktop"));
    await waitFor(() => screen.getByTestId("confirm-stub-Confirmar envío"));

    fireEvent.click(screen.getByText("confirmar-Confirmar envío"));

    await waitFor(() => expect(client.confirmCustomerQuoteSend).toHaveBeenCalledTimes(1));
  });

  it("the drawer's onRequestClose callback opens the real CloseQuoteDialog, and confirming it closes the quote and refetches", async () => {
    const item = globalQuoteItemFixture({
      quote: { ...globalQuoteItemFixture().quote, revision_status: "sent", board_stage: "sent_follow_up" },
    });
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [item],
    });
    vi.mocked(client.closeCustomerQuote).mockResolvedValue({
      ...item.quote,
      revision_status: "closed_won",
      board_stage: "closed",
      quote_outcome: "won",
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("abrir-desktop"));
    fireEvent.click(screen.getByText("abrir-desktop"));
    await waitFor(() => screen.getByTestId("drawer-stub"));
    fireEvent.click(screen.getByText("cerrar-cotizacion-drawer"));

    await waitFor(() => screen.getByTestId("close-quote-dialog"));
    fireEvent.click(screen.getByRole("radio", { name: /^Ganada/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));

    await waitFor(() =>
      expect(client.closeCustomerQuote).toHaveBeenCalledWith(
        item.quote.quote_id,
        { expected_version: item.quote.version, outcome: "won" },
        expect.any(String),
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("close-quote-dialog")).toBeNull());
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
  });
});
