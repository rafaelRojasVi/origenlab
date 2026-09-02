import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QuoteWorkspaceSection } from "./QuoteWorkspaceSection";
import {
  createCustomerQuote,
  fetchCustomerQuotes,
  retryCustomerQuoteDriveWorkspace,
} from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import type { CustomerQuote } from "../../api/customerQuoteTypes";

vi.mock("../../api/customerQuoteClient", () => ({
  fetchCustomerQuotes: vi.fn(),
  createCustomerQuote: vi.fn(),
  retryCustomerQuoteDriveWorkspace: vi.fn(),
}));

const SALES_ID = `sales_${"b".repeat(32)}`;
const QUOTE_ID = `quote_${"a".repeat(32)}`;

function quote(overrides: Partial<CustomerQuote> = {}): CustomerQuote {
  return {
    quote_id: QUOTE_ID,
    sales_opportunity_id: SALES_ID,
    quote_number: "CN011729",
    document_number: "CN00011729",
    quote_origin: "generated",
    sales_opportunity_title: "Centrífuga CEAF",
    status: "draft",
    version: 1,
    latest_revision_number: 1,
    revision_status: "draft",
    revision_updated_by: "tatiana@origenlab.cl",
    revision_updated_at: "2026-08-30T14:00:00+00:00",
    board_stage: "review",
    created_by: "tatiana@origenlab.cl",
    updated_by: "tatiana@origenlab.cl",
    created_at: "2026-08-30T14:00:00+00:00",
    updated_at: "2026-08-30T14:00:00+00:00",
    drive_workspace: {
      provider: "google_drive",
      provisioning_status: "ready",
      folder_id: "folder-1",
      folder_web_url: "https://drive.google.com/drive/folders/folder-1",
      sheet_file_id: "sheet-1",
      sheet_web_url: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
      failure_category: null,
      attempt_count: 1,
      version: 3,
      retryable: true,
      lease_expires_at: null,
      requested_at: "2026-08-30T14:00:00+00:00",
      completed_at: "2026-08-30T14:00:05+00:00",
    },
    ...overrides,
  };
}

function failedQuote(): CustomerQuote {
  return quote({
    drive_workspace: {
      provider: "google_drive",
      provisioning_status: "failed",
      folder_id: null,
      folder_web_url: null,
      sheet_file_id: null,
      sheet_web_url: null,
      failure_category: "drive_unavailable",
      attempt_count: 1,
      version: 3,
      retryable: true,
      lease_expires_at: null,
      requested_at: "2026-08-30T14:00:00+00:00",
      completed_at: null,
    },
  });
}

describe("QuoteWorkspaceSection", () => {
  beforeEach(() => {
    vi.mocked(fetchCustomerQuotes)
      .mockReset()
      .mockResolvedValue({ meta: { count: 0 }, items: [] });
    vi.mocked(createCustomerQuote).mockReset();
    vi.mocked(retryCustomerQuoteDriveWorkspace).mockReset();
  });

  it("lists existing quotes with validated Drive links", async () => {
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [quote()],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(screen.getByText("CN011729")).toBeInTheDocument(),
    );

    const folderLink = screen.getByRole("link", {
      name: "Abrir carpeta en Drive",
    });

    expect(folderLink).toHaveAttribute(
      "href",
      "https://drive.google.com/drive/folders/folder-1",
    );
    expect(folderLink).toHaveAttribute("target", "_blank");
    expect(folderLink.getAttribute("rel") ?? "").toContain("noopener");

    expect(
      screen.getByRole("link", { name: "Abrir plantilla de cotización" }),
    ).toHaveAttribute(
      "href",
      "https://docs.google.com/spreadsheets/d/sheet-1/edit",
    );
  });

  it("shows an empty state when there are no quotes", async () => {
    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(
        screen.getByText("Aún no hay cotizaciones para esta oportunidad."),
      ).toBeInTheDocument(),
    );
  });

  it("creates a quote and blocks duplicate submits while pending", async () => {
    let resolveCreate: (value: CustomerQuote) => void = () => undefined;

    vi.mocked(createCustomerQuote).mockImplementation(
      () =>
        new Promise<CustomerQuote>((resolve) => {
          resolveCreate = resolve;
        }),
    );

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    const button = await screen.findByRole("button", {
      name: "Nueva cotización",
    });

    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    expect(createCustomerQuote).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(screen.getByText("Creando cotización…")).toBeInTheDocument();

    resolveCreate(quote());

    await waitFor(() =>
      expect(screen.getByText("CN011729")).toBeInTheDocument(),
    );
    expect(button).not.toBeDisabled();
  });

  it("shows honest progress when Drive is still pending", async () => {
    vi.mocked(createCustomerQuote).mockResolvedValue(
      quote({
        drive_workspace: {
          provider: "google_drive",
          provisioning_status: "pending",
          folder_id: null,
          folder_web_url: null,
          sheet_file_id: null,
          sheet_web_url: null,
          failure_category: null,
          attempt_count: 1,
          version: 2,
          retryable: true,
          lease_expires_at: null,
          requested_at: "2026-08-30T14:00:00+00:00",
          completed_at: null,
        },
      }),
    );

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Nueva cotización" }),
    );

    await waitFor(() =>
      expect(screen.getByText("CN011729")).toBeInTheDocument(),
    );

    expect(
      screen.getByText("Preparando carpeta en Drive…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Abrir carpeta en Drive" }),
    ).not.toBeInTheDocument();
  });

  it("offers an operator-visible retry action while Drive is pending, in case it is stuck", async () => {
    // A process crash between commit and Drive completion leaves the
    // workspace durably 'pending' with no automatic recovery -- the
    // operator must be able to trigger a fresh provisioning attempt from
    // here, not only from the 'failed' state.
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [
        quote({
          drive_workspace: {
            provider: "google_drive",
            provisioning_status: "pending",
            folder_id: null,
            folder_web_url: null,
            sheet_file_id: null,
            sheet_web_url: null,
            failure_category: null,
            attempt_count: 1,
            version: 2,
            retryable: true,
            lease_expires_at: null,
            requested_at: "2026-08-30T14:00:00+00:00",
            completed_at: null,
          },
        }),
      ],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(
        screen.getByText("Preparando carpeta en Drive…"),
      ).toBeInTheDocument(),
    );

    vi.mocked(retryCustomerQuoteDriveWorkspace).mockResolvedValue(quote());

    fireEvent.click(
      screen.getByRole("button", { name: "Reintentar creación en Drive" }),
    );

    await waitFor(() =>
      expect(retryCustomerQuoteDriveWorkspace).toHaveBeenCalledWith(QUOTE_ID, {
        expected_version: 2,
      }),
    );

    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Abrir carpeta en Drive" }),
      ).toBeInTheDocument(),
    );
  });

  it("does not offer a retry action while an attempt actively owns the lease", async () => {
    // The server marks retryable: false while an attempt is actively
    // in-flight -- offering a retry here would only conflict, so no button
    // must be rendered at all.
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [
        quote({
          drive_workspace: {
            provider: "google_drive",
            provisioning_status: "pending",
            folder_id: null,
            folder_web_url: null,
            sheet_file_id: null,
            sheet_web_url: null,
            failure_category: null,
            attempt_count: 1,
            version: 2,
            retryable: false,
            lease_expires_at: "2026-08-30T14:05:00+00:00",
            requested_at: "2026-08-30T14:00:00+00:00",
            completed_at: null,
          },
        }),
      ],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(screen.getByText("CN011729")).toBeInTheDocument(),
    );

    expect(
      screen.queryByRole("button", { name: "Reintentar creación en Drive" }),
    ).not.toBeInTheDocument();
  });

  it("shows a redacted message for a credentials failure, never provider details", async () => {
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [
        quote({
          drive_workspace: {
            provider: "google_drive",
            provisioning_status: "failed",
            folder_id: null,
            folder_web_url: null,
            sheet_file_id: null,
            sheet_web_url: null,
            failure_category: "drive_credentials_invalid",
            attempt_count: 1,
            version: 3,
            retryable: true,
            lease_expires_at: null,
            requested_at: "2026-08-30T14:00:00+00:00",
            completed_at: null,
          },
        }),
      ],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(
        screen.getByText(
          "Las credenciales de Google Drive no son válidas. Avisa al administrador del sistema.",
        ),
      ).toBeInTheDocument(),
    );
  });

  it("shows a human warning and retry action on Drive failure", async () => {
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [failedQuote()],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(
        screen.getByText(
          "La cotización se guardó, pero la carpeta en Drive no se pudo crear.",
        ),
      ).toBeInTheDocument(),
    );

    expect(
      screen.getByText("Google Drive no está disponible en este momento."),
    ).toBeInTheDocument();

    vi.mocked(retryCustomerQuoteDriveWorkspace).mockResolvedValue(quote());

    fireEvent.click(
      screen.getByRole("button", { name: "Reintentar creación en Drive" }),
    );

    await waitFor(() =>
      expect(retryCustomerQuoteDriveWorkspace).toHaveBeenCalledWith(QUOTE_ID, {
        expected_version: 3,
      }),
    );

    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Abrir carpeta en Drive" }),
      ).toBeInTheDocument(),
    );
  });

  it("keeps technical identifiers inside a disclosure", async () => {
    vi.mocked(fetchCustomerQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [failedQuote()],
    });

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(screen.getByText("CN011729")).toBeInTheDocument(),
    );

    const disclosure = screen.getByText("Detalles técnicos");

    expect(disclosure.closest("details")).not.toBeNull();
    expect(
      screen.getByText((content) => content.includes("drive_unavailable")),
    ).toBeInTheDocument();
  });

  it("reuses the same idempotency key when retrying a failed create", async () => {
    vi.mocked(createCustomerQuote)
      .mockRejectedValueOnce(new OperatorApiError("HTTP 502", 502))
      .mockResolvedValueOnce(quote());

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    const button = await screen.findByRole("button", {
      name: "Nueva cotización",
    });

    fireEvent.click(button);

    await waitFor(() =>
      expect(
        screen.getByText("No pudimos crear la cotización. Reintenta."),
      ).toBeInTheDocument(),
    );

    fireEvent.click(button);

    await waitFor(() =>
      expect(createCustomerQuote).toHaveBeenCalledTimes(2),
    );

    const [, firstKey] = vi.mocked(createCustomerQuote).mock.calls[0];
    const [, secondKey] = vi.mocked(createCustomerQuote).mock.calls[1];

    expect(firstKey).toBe(secondKey);
  });

  it("explains when quote numbering is not activated yet", async () => {
    vi.mocked(createCustomerQuote).mockRejectedValue(
      new OperatorApiError(
        '{"error":{"message":"quote_numbering_not_configured: quote numbering has not been activated"}}',
        503,
      ),
    );

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Nueva cotización" }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(
          "La numeración de cotizaciones aún no está activada. Avisa al administrador del sistema.",
        ),
      ).toBeInTheDocument(),
    );
  });

  it("shows a load error without breaking the drawer", async () => {
    vi.mocked(fetchCustomerQuotes).mockRejectedValue(
      new OperatorApiError("HTTP 500", 500),
    );

    render(<QuoteWorkspaceSection salesOpportunityId={SALES_ID} />);

    await waitFor(() =>
      expect(
        screen.getByText("No pudimos cargar las cotizaciones."),
      ).toBeInTheDocument(),
    );
  });
});
