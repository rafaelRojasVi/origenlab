import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, PipelineResponse } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import type { Invoice, InvoiceList } from "./invoiceApi";

// The toaster is mounted by the CRM shell; read what the page asked it to say.
const toasts = vi.hoisted(() => [] as string[]);
vi.mock("../ui", async (importActual) => {
  const actual = await importActual<typeof import("../ui")>();
  return { ...actual, toast: (text: string) => { toasts.push(text); } };
});

vi.mock("./invoiceApi", async (importActual) => {
  const actual = await importActual<typeof import("./invoiceApi")>();
  return {
    ...actual,
    fetchInvoices: vi.fn(),
    importInvoices: vi.fn(),
    setInvoiceCase: vi.fn(),
    markInvoicePaid: vi.fn(),
    markInvoiceUnpaid: vi.fn(),
  };
});
vi.mock("../caseDocuments", async (importActual) => {
  const actual = await importActual<typeof import("../caseDocuments")>();
  return { ...actual, fetchCaseDocumentUrl: vi.fn() };
});
vi.mock("../crmApi", async (importActual) => {
  const actual = await importActual<typeof import("../crmApi")>();
  return { ...actual, fetchPipeline: vi.fn() };
});

import * as caseDocuments from "../caseDocuments";
import * as crmApi from "../crmApi";
import * as api from "./invoiceApi";
import { PagosPage } from "./PagosPage";

// Every value is invented; the repository is public.
const ORG = "aaaaaaaa-1111-4111-8111-111111111111";
const CASE = "bbbbbbbb-2222-4222-8222-222222222222";

function invoice(invoice_id: string, folio: number, extra: Partial<Invoice> = {}): Invoice {
  return {
    invoice_id, version: 1, doc_type: 33, folio, issued_on: "2026-10-09", days_open: 19, age_bucket: "0-30",
    client_rut: "12345678-5", client_name: "INSTITUTO FICTICIO", organization: { id: ORG, name: "Instituto Ficticio" },
    net_amount: 100000, vat_amount: 19000, exempt_amount: 0, total_amount: 119000, balance: 119000, reference: null,
    notes: [], status: "unpaid", paid_on: null, case_link: null, opportunity: null, ...extra,
  };
}

const LIST: InvoiceList = {
  items: [
    invoice("i-1", 9001),
    invoice("i-2", 9002, {
      case_link: "machine", opportunity: { id: CASE, title: "Balanza ficticia", stage: "won", quote_number: "CN09876" },
      balance: 100000, notes: [{ invoice_id: "n-1", doc_type: 61, folio: 9003, issued_on: "2026-10-20", total_amount: 19000 }],
    }),
    invoice("i-3", 9004, { status: "paid", paid_on: "2026-10-15", age_bucket: null }),
  ],
  summary: {
    por_cobrar: 219000, unpaid_count: 2, by_age: { "0-30": 219000, "31-60": 0, "61-90": 0, "90+": 0 },
    without_case: 2, last_issued_on: "2026-10-09",
  },
  today: "2026-10-28",
};

const CARD = {
  opportunity_id: CASE, title: "Balanza ficticia", stage: "won", quote_numbers: ["CN09876"], latest_revision: null,
  organization: { organization_id: ORG, name: "Instituto Ficticio" },
  documents: [{ document_id: "d-1", quote_revision_id: null, kind: "purchase_order", document_number: "4500012345",
    original_filename: "oc-ficticia.pdf", content_type: "application/pdf", byte_size: 1000, source: "upload",
    attached_at: "2026-10-05T12:00:00Z", attached_by: "Ventas" }],
  quotes: [{ quote_id: "q-1", quote_number: "CN09876", revisions: [{
    revision_id: "r-1", revision_no: 1, status: "sent", origin: "historical_import", sent_at: "2026-10-01T12:00:00Z",
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "CN09876",
    contents: { currency: "CLP", net_total: "84034", net_source: "document", tax_total: null, gross_total: null,
      lines: [{ line_no: 1, item: "1", parent_item: null, kind: "product", brand: "Marca", model: "BX-1",
        description: "Balanza ficticia", qty: "2", unit_price: "42017", line_total: "84034", optional: false, check_status: "ok" }] },
  }] }],
} as unknown as OpportunityCardData;

const fetchInvoices = vi.mocked(api.fetchInvoices);
const importInvoices = vi.mocked(api.importInvoices);
const setInvoiceCase = vi.mocked(api.setInvoiceCase);
const markInvoicePaid = vi.mocked(api.markInvoicePaid);

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "v@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  } as AuthSessionState;
}

function renderPage(role = "sales") {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <PagosPage navigate={navigate} />
    </AuthSessionContext.Provider>,
  );
  return { navigate };
}

beforeEach(() => {
  fetchInvoices.mockResolvedValue(LIST);
  vi.mocked(crmApi.fetchPipeline).mockResolvedValue({ items: [CARD], total: 1, drive_configured: false } as PipelineResponse);
  setInvoiceCase.mockResolvedValue({});
  markInvoicePaid.mockResolvedValue({});
});

afterEach(() => {
  vi.clearAllMocks();
  toasts.length = 0;
  clearResourceCache();
});

describe("«Pagos»", () => {
  it("shows what is owed, by age, and lists only the unpaid facturas first", async () => {
    renderPage();
    expect(await screen.findAllByText("$ 219.000")).toHaveLength(2); // the total, and all of it in 0–30 días
    expect(screen.getByText("Factura 9001")).toBeInTheDocument();
    expect(screen.getByText("Factura 9002")).toBeInTheDocument();
    expect(screen.queryByText("Factura 9004")).not.toBeInTheDocument();
    // The nota de crédito sits under its factura and the saldo is the API's.
    expect(screen.getByText(/Nota de crédito 9003/)).toBeInTheDocument();
    expect(screen.getByText("automático")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Pagadas/ }));
    expect(screen.getByText("Factura 9004")).toBeInTheDocument();
  });

  it("attaches a case from the client's own cases", async () => {
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Asignar caso…" }))[0]);
    const dialog = screen.getByRole("dialog", { name: "Asignar caso" });
    expect(within(dialog).getByRole("searchbox", { name: "Buscar caso" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: /CN09876/ }));
    await waitFor(() => expect(setInvoiceCase).toHaveBeenCalledWith({ invoice_id: "i-1", invoice_version: 1, opportunity_id: CASE }));
    expect(toasts).toContain("Caso asignado");
  });

  it("marks a factura paid on the day chosen", async () => {
    renderPage();
    const row = (await screen.findAllByTestId("invoice-row")).find((r) => within(r).queryByText("Factura 9001")) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Marcar pagada…" }));
    const day = within(row).getByLabelText("¿Qué día pagó?");
    expect(day).toHaveFocus();
    fireEvent.change(day, { target: { value: "2026-10-29" } }); // tomorrow: refused before any call
    expect(within(row).getByRole("button", { name: "Guardar pago" })).toBeDisabled();
    fireEvent.change(day, { target: { value: "2026-10-27" } });
    fireEvent.click(within(row).getByRole("button", { name: "Guardar pago" }));
    await waitFor(() => expect(markInvoicePaid).toHaveBeenCalledWith({ invoice_id: "i-1", invoice_version: 1, paid_on: "2026-10-27" }));
  });

  it("uploads the SII CSV and says what came in; a wrong file reads the API's Spanish reason", async () => {
    importInvoices.mockResolvedValueOnce({ imported: 4, already_on_record: 1, attached_to_case: 2, without_organization: 0, skipped_types: {} });
    renderPage();
    const input = await screen.findByLabelText("Archivo CSV del Registro de Ventas");
    fireEvent.change(input, { target: { files: [new File(["x"], "RCV_VENTA.csv", { type: "text/csv" })] } });
    await waitFor(() => expect(toasts).toContain("4 documentos nuevos · 1 ya estaba · 2 asignados a su caso"));
    // The result stays on the page after the toast goes.
    expect(screen.getByRole("status")).toHaveTextContent("Listo: 4 documentos nuevos · 1 ya estaba · 2 asignados a su caso.");
    importInvoices.mockRejectedValueOnce(new OperatorApiError(JSON.stringify({ detail: { code: "sii_file_invalid",
      message: "Este es el registro de COMPRAS. Sube el de VENTAS (RCV_VENTA_…)." } }), 422));
    fireEvent.change(input, { target: { files: [new File(["x"], "RCV_COMPRA.csv", { type: "text/csv" })] } });
    await waitFor(() => expect(toasts).toContain("Este es el registro de COMPRAS. Sube el de VENTAS (RCV_VENTA_…)."));
  });

  it("a viewer reads but cannot upload, assign or mark paid", async () => {
    renderPage("viewer");
    expect(await screen.findByText("Factura 9001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Subir CSV del SII" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Asignar caso…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Marcar pagada…" })).not.toBeInTheDocument();
  });

  it("lists what is owed oldest first, and «Sin caso» narrows to the facturas without one", async () => {
    fetchInvoices.mockResolvedValue({ ...LIST, items: [invoice("i-1", 9001, { days_open: 5 }), invoice("i-5", 9005, { days_open: 95 }), ...LIST.items.slice(1)] });
    renderPage();
    const rows = await screen.findAllByTestId("invoice-row");
    expect(within(rows[0]).getByText("Factura 9005")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/hace 95 días/)).toHaveClass("text-bad");
    fireEvent.click(screen.getByRole("button", { name: /facturas sin caso/ }));
    expect(screen.getAllByTestId("invoice-row")).toHaveLength(3); // the two unpaid ones and the paid 9004
    expect(screen.queryByText("Factura 9002")).not.toBeInTheDocument();
  });

  it("keeps the download steps one click away when there are facturas", async () => {
    renderPage();
    const help = await screen.findByRole("button", { name: "¿Cómo descargo el archivo?" });
    expect(help).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(help);
    expect(screen.getByText(/Descargar Detalles/)).toBeInTheDocument();
  });

  it("explains how to download the register when there is nothing yet", async () => {
    fetchInvoices.mockResolvedValue({ ...LIST, items: [] });
    renderPage();
    expect(await screen.findByText("Todavía no hay facturas")).toBeInTheDocument();
    expect(screen.getByText(/Descargar Detalles/)).toBeInTheDocument();
  });

  it("opens a factura: folio, client, the quote's lines, amounts and the case's OC", async () => {
    fetchInvoices.mockResolvedValue({ ...LIST, items: [invoice("i-2", 9002, {
      case_link: "machine", opportunity: { id: CASE, title: "Balanza ficticia", stage: "won", quote_number: "CN09876" },
      net_amount: 84034, vat_amount: 15966, total_amount: 100000, balance: 100000 })] });
    vi.mocked(caseDocuments.fetchCaseDocumentUrl).mockResolvedValue("https://example.invalid/oc.pdf");
    const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    renderPage();
    const row = (await screen.findAllByTestId("invoice-row"))[0];
    expect(await within(row).findByText(/OC 4500012345/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Ver la Factura 9002" }));
    const sheet = screen.getByRole("dialog", { name: "Factura electrónica Nº 9002" });
    expect(within(sheet).getByTestId("invoice-summary")).toHaveTextContent("Te debe$ 100.000");
    expect(within(sheet).getByText(/RUT 12345678-5/)).toBeInTheDocument();
    expect(within(sheet).getByText(/Marca · BX-1 · Balanza ficticia/)).toBeInTheDocument();
    expect(within(sheet).getByText("Mismo neto que la factura")).toBeInTheDocument();
    expect(within(sheet).getByText(/asignado solo: el neto calza con la cotización/)).toBeInTheDocument();
    expect(within(sheet).getByText("$ 15.966")).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole("button", { name: "Ver la orden de compra 4500012345" }));
    await waitFor(() => expect(tab.location.href).toBe("https://example.invalid/oc.pdf"));
    expect(caseDocuments.fetchCaseDocumentUrl).toHaveBeenCalledWith(CASE, "d-1");
  });

  it("a factura without a case says there is no OC or quote to show", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Ver la Factura 9001" }));
    const sheet = screen.getByRole("dialog", { name: "Factura electrónica Nº 9001" });
    expect(within(sheet).getByText(/Sin caso todavía/)).toBeInTheDocument();
    expect(within(sheet).getByText(/no envía los productos/)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /Nº 9001/ })).not.toBeInTheDocument();
  });

  it("marks it paid from the factura itself", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Ver la Factura 9001" }));
    const sheet = screen.getByRole("dialog", { name: "Factura electrónica Nº 9001" });
    fireEvent.click(within(sheet).getByRole("button", { name: "Marcar pagada…" }));
    fireEvent.change(within(sheet).getByLabelText("¿Qué día pagó?"), { target: { value: "2026-10-27" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Guardar pago" }));
    await waitFor(() => expect(markInvoicePaid).toHaveBeenCalledWith({ invoice_id: "i-1", invoice_version: 1, paid_on: "2026-10-27" }));
  });
});
