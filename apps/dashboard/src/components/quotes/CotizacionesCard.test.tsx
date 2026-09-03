import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  CustomerQuote,
  CustomerQuoteDriveWorkspace,
  CustomerQuoteGlobalItem,
} from "../../api/customerQuoteTypes";
import { CotizacionesCard } from "./CotizacionesCard";

function workspace(
  overrides: Partial<CustomerQuoteDriveWorkspace> = {},
): CustomerQuoteDriveWorkspace {
  return {
    provider: "google_drive",
    provisioning_status: "folder_ready",
    folder_id: "folder-1",
    folder_web_url: "https://drive.google.com/drive/folders/folder-1",
    sheet_file_id: null,
    sheet_web_url: null,
    failure_category: null,
    attempt_count: 0,
    version: 1,
    retryable: true,
    lease_expires_at: null,
    requested_at: null,
    completed_at: "2026-09-02T12:00:00+00:00",
    ...overrides,
  };
}

function quote(overrides: Partial<CustomerQuote> = {}): CustomerQuote {
  return {
    quote_id: "quote_" + "a".repeat(32),
    sales_opportunity_id: "sales_" + "b".repeat(32),
    quote_number: "01191-26",
    document_number: "CN01191",
    quote_origin: "adopted",
    sales_opportunity_title: "Centrífuga CEAF",
    status: "draft",
    version: 1,
    latest_revision_number: 1,
    revision_status: "draft",
    revision_updated_by: "tatiana@origenlab.cl",
    revision_updated_at: "2026-09-02T12:00:00+00:00",
    board_stage: "review",
    quote_outcome: null,
    created_by: "tatiana@origenlab.cl",
    updated_by: "tatiana@origenlab.cl",
    created_at: "2026-09-02T12:00:00+00:00",
    updated_at: "2026-09-02T12:00:00+00:00",
    drive_workspace: workspace(),
    ...overrides,
  };
}

function item(overrides: Partial<CustomerQuoteGlobalItem> = {}): CustomerQuoteGlobalItem {
  return {
    quote: quote(),
    sales_opportunity_stage: "quoting",
    sales_opportunity_owner_key: "tatiana@origenlab.cl",
    organization_display_name: "CEAF",
    contact_display_name: "Marcela Rivas",
    contact_primary_email: "marcela@ceaf.cl",
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

describe("CotizacionesCard", () => {
  it("renders an adopted (folder_ready) quote's Drive state as 'Carpeta lista', not 'Drive listo'", () => {
    render(
      <CotizacionesCard
        item={item({
          quote: quote({
            quote_origin: "adopted",
            drive_workspace: workspace({ provisioning_status: "folder_ready" }),
          }),
        })}
        onOpen={vi.fn()}
        dragDisabled={false}
      />,
    );

    expect(screen.getByText("Carpeta lista")).toBeTruthy();
    expect(screen.queryByText("Drive listo")).toBeNull();
  });

  it("renders a fully-provisioned generated quote's Drive state as 'Drive listo'", () => {
    render(
      <CotizacionesCard
        item={item({
          quote: quote({
            quote_origin: "generated",
            drive_workspace: workspace({
              provisioning_status: "ready",
              sheet_file_id: "sheet-1",
              sheet_web_url: "https://docs.google.com/spreadsheets/d/sheet-1",
            }),
          }),
        })}
        onOpen={vi.fn()}
        dragDisabled={false}
      />,
    );

    expect(screen.getByText("Drive listo")).toBeTruthy();
    expect(screen.queryByText("Carpeta lista")).toBeNull();
  });

  it("shows a distinct 'Borrador' badge for a draft review-lane quote, without opening the drawer", () => {
    render(
      <CotizacionesCard
        item={item({ quote: quote({ revision_status: "draft", board_stage: "review" }) })}
        onOpen={vi.fn()}
        dragDisabled={false}
      />,
    );

    expect(screen.getByText("Borrador")).toBeTruthy();
  });

  it("shows a distinct 'Ajustes solicitados' badge for adjustments_requested, not confusable with draft", () => {
    render(
      <CotizacionesCard
        item={item({ quote: quote({ revision_status: "adjustments_requested", board_stage: "review" }) })}
        onOpen={vi.fn()}
        dragDisabled={false}
      />,
    );

    expect(screen.getByText("Ajustes solicitados")).toBeTruthy();
    expect(screen.queryByText("Borrador")).toBeNull();
  });

  it("shows a distinct 'Lista para aprobación' badge for pending_approval, not confusable with the other two review sub-states", () => {
    render(
      <CotizacionesCard
        item={item({ quote: quote({ revision_status: "pending_approval", board_stage: "review" }) })}
        onOpen={vi.fn()}
        dragDisabled={false}
      />,
    );

    expect(screen.getByText("Lista para aprobación")).toBeTruthy();
    expect(screen.queryByText("Borrador")).toBeNull();
    expect(screen.queryByText("Ajustes solicitados")).toBeNull();
  });
});
