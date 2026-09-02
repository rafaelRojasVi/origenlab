import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdoptDriveFolderModal } from "./AdoptDriveFolderModal";
import * as commercialClient from "../../api/commercialOperationsClient";
import * as quoteClient from "../../api/customerQuoteClient";
import { drivePendingQuoteItemFixture, globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

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

describe("AdoptDriveFolderModal", () => {
  beforeEach(() => {
    vi.mocked(commercialClient.fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(commercialClient.createManualSalesOpportunity).mockReset();
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockReset();
  });

  it("prefills document_number from the discovered document identifier, editable", () => {
    const driveItem = drivePendingQuoteItemFixture({ document_identifier: "CN01191" });
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    const input = screen.getByLabelText("Número de documento") as HTMLInputElement;
    expect(input.value).toBe("CN01191");
    expect(input).not.toBeDisabled();
  });

  it("never prefills quote_number from document_number", () => {
    const driveItem = drivePendingQuoteItemFixture({ document_identifier: "CN01191" });
    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    const input = screen.getByLabelText("Número de cotización") as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("disables submit until an opportunity is selected and quote_number is filled", async () => {
    const driveItem = drivePendingQuoteItemFixture();
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });

    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    const submitButton = screen.getByRole("button", { name: "Incorporar al CRM" });
    expect(submitButton).toBeDisabled();

    await waitFor(() => screen.getByText("Balanza analítica"));
    fireEvent.click(screen.getByText("Balanza analítica"));
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    expect(submitButton).not.toBeDisabled();
  });

  it("submits with the selected opportunity, document_number, quote_number, and the folder's existing id/url", async () => {
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

  it("never calls any Drive-provisioning client function -- adoption is Postgres-only", async () => {
    const driveItem = drivePendingQuoteItemFixture();
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);
    await waitFor(() => screen.getByText("Balanza analítica"));
    fireEvent.click(screen.getByText("Balanza analítica"));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalled());

    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
    expect(quoteClient.retryCustomerQuoteDriveWorkspace).not.toHaveBeenCalled();
  });

  it("manual tab: creates the opportunity first, then adopts against it", async () => {
    const driveItem = drivePendingQuoteItemFixture();
    const created = { sales_opportunity_id: "sales_" + "d".repeat(32) };
    vi.mocked(commercialClient.createManualSalesOpportunity).mockResolvedValue(created as never);
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity({ sales_opportunity_id: created.sales_opportunity_id })],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockResolvedValue(globalQuoteItemFixture().quote);

    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={vi.fn()} />);

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza nueva" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "CEAF" } });
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });

    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => expect(commercialClient.createManualSalesOpportunity).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(quoteClient.adoptCustomerQuoteDriveFolder).toHaveBeenCalledTimes(1));
    expect(vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mock.calls[0][0]).toBe(created.sales_opportunity_id);
  });

  it("shows an error message and stays open when adoption fails", async () => {
    const driveItem = drivePendingQuoteItemFixture();
    vi.mocked(commercialClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });
    vi.mocked(quoteClient.adoptCustomerQuoteDriveFolder).mockRejectedValue(new Error("conflict"));
    const onAdopted = vi.fn();

    render(<AdoptDriveFolderModal item={driveItem} open onClose={vi.fn()} onAdopted={onAdopted} />);
    await waitFor(() => screen.getByText("Balanza analítica"));
    fireEvent.click(screen.getByText("Balanza analítica"));
    fireEvent.change(screen.getByLabelText("Número de cotización"), { target: { value: "01191-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Incorporar al CRM" }));

    await waitFor(() => screen.getByRole("alert"));
    expect(onAdopted).not.toHaveBeenCalled();
  });

  it("renders nothing when item is null", () => {
    render(<AdoptDriveFolderModal item={null} open onClose={vi.fn()} onAdopted={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
