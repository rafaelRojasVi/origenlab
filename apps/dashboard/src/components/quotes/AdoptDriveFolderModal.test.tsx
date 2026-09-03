import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdoptDriveFolderModal } from "./AdoptDriveFolderModal";
import * as commercialClient from "../../api/commercialOperationsClient";
import * as quoteClient from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import { drivePendingQuoteItemFixture, globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";
import type { CustomerQuoteIntakeResolution } from "../../api/customerQuoteTypes";

vi.mock("../../api/commercialOperationsClient");
vi.mock("../../api/customerQuoteClient");

function opportunity(overrides: Partial<SalesOpportunityListItem> = {}): SalesOpportunityListItem {
  return {
    sales_opportunity_id: "sales_" + "c".repeat(32),
    source_kind: "pr3",
    source_opportunity_id: "o_1",
    account_id: null,
    primary_contact_id: null,
    organization_id: null,
    primary_crm_contact_id: null,
    title: "Balanza analítica",
    stage: "quoting",
    owner_key: "tatiana@origenlab.cl",
    version: 1,
    created_by: "tatiana@origenlab.cl",
    updated_by: "tatiana@origenlab.cl",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    stage_updated_at: "2026-09-01T10:00:00Z",
    contact_display_email: null,
    account_display_domain: null,
    organization_display_name: "CEAF",
    contact_display_name: null,
    contact_primary_email: null,
    open_task_count: 0,
    next_task_id: null,
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

function emptyResolution(overrides: Partial<CustomerQuoteIntakeResolution> = {}): CustomerQuoteIntakeResolution {
  return {
    document_number_candidate: "CN01191",
    document_number_conflict: false,
    organization: null,
    contacts: [],
    opportunity: null,
    quote_number_resolved: false,
    ...overrides,
  };
}

describe("AdoptDriveFolderModal", () => {
  beforeEach(() => {
    vi.mocked(commercialClient.fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(commercialClient.createManualSalesOpportunity).mockReset();
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockReset();
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockReset().mockResolvedValue(emptyResolution());
  });

  it("renders nothing when item is null", () => {
    render(<AdoptDriveFolderModal item={null} open onClose={vi.fn()} onAdopted={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("calls fetchDriveIntakeResolution with the folder name on open", async () => {
    const driveItem = drivePendingQuoteItemFixture({ folder_name: "CN01191-ICN Chile" });
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => expect(quoteClient.fetchDriveIntakeResolution).toHaveBeenCalledWith("CN01191-ICN Chile"));
  });

  it("prefills Documento from the resolved document_number_candidate", async () => {
    const driveItem = drivePendingQuoteItemFixture({ document_identifier: "CN01191" });
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => {
      const input = screen.getByLabelText("Documento") as HTMLInputElement;
      expect(input.value).toBe("CN01191");
    });
    expect(screen.getByText(/Detectado desde Drive/)).toBeInTheDocument();
  });

  it("shows a conflict warning when document_number_conflict is true", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({ document_number_conflict: true }),
    );
    const driveItem = drivePendingQuoteItemFixture();
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByText(/Ya existe una cotización con este número de documento/));
  });

  it("never prefills Número de cotización -- always blank and required", async () => {
    const driveItem = drivePendingQuoteItemFixture();
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByLabelText("Documento"));
    const input = screen.getByLabelText("Número de cotización") as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("a confirmed organization match auto-selects it and shows no create-new form", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [{ source: "durable_crm", reason: "normalized_name_match", detail: "..." }],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByText("ICN Chile"));
    expect(screen.getByText(/Coincidencia encontrada/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Organización")).toBeNull();
  });

  it("an ambiguous organization match renders a picker instead of auto-selecting", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: null,
          display_name: "ICN Chile",
          confidence: "possible_match",
          evidence: [],
          alternates: [
            { organization_id: "org_a", display_name: "ICN Chile SPA" },
            { organization_id: "org_b", display_name: "ICN Chile Ltda" },
          ],
        },
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => expect(screen.getAllByRole("radio")).toHaveLength(2));
  });

  it("an unresolved organization renders an editable create-new field prefilled from the folder name", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: null,
          display_name: "ICN Chile",
          confidence: "unresolved",
          evidence: [{ source: "drive_folder_name", reason: "no_crm_or_email_evidence", detail: "..." }],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => {
      const input = screen.getByLabelText("Organización") as HTMLInputElement;
      expect(input.value).toBe("ICN Chile");
    });
  });

  it("submitting an auto-create opportunity uses the resolved organization_id, not a display name", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );
    vi.mocked(commercialClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: "sales_new",
    } as never);
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity({ sales_opportunity_id: "sales_new" })],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByDisplayValue("ICN Chile — Cotización"));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-26" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() =>
      expect(commercialClient.createManualSalesOpportunity).toHaveBeenCalledWith(
        expect.objectContaining({ organization_id: "org_icn" }),
        expect.any(String),
      ),
    );
    expect(commercialClient.createManualSalesOpportunity).toHaveBeenCalledWith(
      expect.not.objectContaining({ organization_display_name: expect.anything() }),
      expect.any(String),
    );
  });

  it("an existing resolved opportunity skips creation and adopts directly under it", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        opportunity: {
          sales_opportunity_id: "sales_existing",
          title: "ICN Chile deal",
          confidence: "confirmed_durable_match",
        },
      }),
    );
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);
    const onAdopted = vi.fn();

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={onAdopted} />);

    await waitFor(() => screen.getByText(/Oportunidad existente encontrada/));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-26" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalledTimes(1));
    expect(commercialClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mock.calls[0][0]).toBe("sales_existing");
    expect(onAdopted).toHaveBeenCalled();
  });

  it("Gmail contact evidence prefills contact name/email as editable fields", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        contacts: [
          {
            contact_id: null,
            display_name: "Ana Example",
            email: "ana.example@icn.example",
            confidence: "possible_match",
            evidence: [{ source: "gmail_history", reason: "gmail_contact_history", detail: "Encontrado en 8 correos" }],
          },
        ],
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => {
      expect((screen.getByLabelText("Nombre de contacto") as HTMLInputElement).value).toBe("Ana Example");
      expect((screen.getByLabelText("Correo de contacto") as HTMLInputElement).value).toBe("ana.example@icn.example");
    });
    expect(screen.getByText(/Encontrado en 8 correos/)).toBeInTheDocument();
  });

  it('"Cambiar datos" switches to the manual existing/new-opportunity flow', async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: null, title: "ICN Chile — Cotización", confidence: "unresolved" },
      }),
    );
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByText(/Coincidencia encontrada/));
    fireEvent.click(screen.getByRole("button", { name: "Cambiar datos" }));

    expect(screen.getByRole("tablist", { name: "Origen de la oportunidad" })).toBeInTheDocument();
  });

  it("override mode: submits with the selected opportunity, document_number, quote_number, and the folder's existing id/url", async () => {
    const driveItem = drivePendingQuoteItemFixture({
      folder_id: "drive-folder-1191",
      folder_web_url: "https://drive.google.com/drive/folders/drive-folder-1191",
      document_identifier: "CN01191",
    });
    const opp = opportunity();
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opp],
    });
    const adoptedQuote = globalQuoteItemFixture().quote;
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(adoptedQuote);
    const onAdopted = vi.fn();

    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={onAdopted} />);

    // Default resolution (no organization detected) falls into override
    // mode automatically -- no "Cambiar datos" click needed.
    await waitFor(() => screen.getByText("Balanza analítica"));
    fireEvent.click(screen.getByText("Balanza analítica"));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });

    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalledTimes(1));
    const [salesOpportunityId, command] = vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mock.calls[0];
    expect(salesOpportunityId).toBe(opp.sales_opportunity_id);
    expect(command).toEqual({
      document_number: "CN01191",
      quote_number: "01191-24",
      folder_id: "drive-folder-1191",
      folder_web_url: "https://drive.google.com/drive/folders/drive-folder-1191",
    });

    expect(onAdopted).toHaveBeenCalled();
  });

  it("override mode: never calls any Drive-provisioning client function -- adoption is Postgres-only", async () => {
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);
    await waitFor(() => screen.getByText("Balanza analítica"));
    fireEvent.click(screen.getByText("Balanza analítica"));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalled());

    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
    expect(quoteClient.retryCustomerQuoteDriveWorkspace).not.toHaveBeenCalled();
  });

  it("override mode, manual tab: creates the opportunity first, then adopts against it", async () => {
    const created = { sales_opportunity_id: "sales_" + "d".repeat(32) };
    vi.mocked(commercialClient.createManualSalesOpportunity).mockResolvedValue(created as never);
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity({ sales_opportunity_id: created.sales_opportunity_id })],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza nueva" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "CEAF" } });
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });

    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(commercialClient.createManualSalesOpportunity).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalledTimes(1));
    expect(vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mock.calls[0][0]).toBe(created.sales_opportunity_id);
  });

  it("shows a specific, actionable message (not a generic catch-all) when adoption fails with a known reason code", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: "sales_existing", title: "ICN Chile deal", confidence: "confirmed_durable_match" },
      }),
    );
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockRejectedValue(
      new OperatorApiError(
        JSON.stringify({ detail: "duplicate_document_number: document_number is already used by another quote" }),
        409,
      ),
    );
    const onAdopted = vi.fn();

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={onAdopted} />);
    await waitFor(() => screen.getByText(/Oportunidad existente encontrada/));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert")).toHaveTextContent(/número de documento ya está en uso/i);
    expect(onAdopted).not.toHaveBeenCalled();
  });

  it("shows a safe generic conflict message for an unrecognized reason code, never the raw code", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockResolvedValue(
      emptyResolution({
        organization: {
          organization_id: "org_icn",
          display_name: "ICN Chile",
          confidence: "confirmed_durable_match",
          evidence: [],
          alternates: [],
        },
        opportunity: { sales_opportunity_id: "sales_existing", title: "ICN Chile deal", confidence: "confirmed_durable_match" },
      }),
    );
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockRejectedValue(
      new OperatorApiError(JSON.stringify({ detail: "some_unmapped_future_reason: internal detail" }), 409),
    );
    const onAdopted = vi.fn();

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={onAdopted} />);
    await waitFor(() => screen.getByText(/Oportunidad existente encontrada/));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert")).not.toHaveTextContent("some_unmapped_future_reason");
    expect(onAdopted).not.toHaveBeenCalled();
  });

  it("a resolution fetch failure falls back to the manual override flow with a visible error", async () => {
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockRejectedValue(
      new OperatorApiError("Commercial operations writes are disabled", 503),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("tablist", { name: "Origen de la oportunidad" })).toBeInTheDocument();
  });

  it("submit is disabled while the resolution is still loading", () => {
    let resolvePromise: (value: CustomerQuoteIntakeResolution) => void = () => undefined;
    vi.mocked(quoteClient.fetchDriveIntakeResolution).mockReturnValue(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }),
    );

    render(<AdoptDriveFolderModal item={drivePendingQuoteItemFixture()} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Incorporar al CRM" })).toBeDisabled();
    resolvePromise(emptyResolution());
  });
});
