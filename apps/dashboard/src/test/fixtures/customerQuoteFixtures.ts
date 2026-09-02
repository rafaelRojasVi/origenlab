import type {
  CustomerQuoteGlobalItem,
  DrivePendingQuoteItem,
} from "../../api/customerQuoteTypes";

export function globalQuoteItemFixture(
  overrides: Partial<CustomerQuoteGlobalItem> = {},
): CustomerQuoteGlobalItem {
  return {
    quote: {
      quote_id: "quote_" + "a".repeat(32),
      sales_opportunity_id: "sales_" + "b".repeat(32),
      quote_number: "01183-26",
      document_number: "CN01183",
      quote_origin: "generated",
      sales_opportunity_title: "Centrífuga CEAF",
      status: "draft",
      version: 1,
      latest_revision_number: 1,
      revision_status: "draft",
      revision_updated_by: "op@origenlab.cl",
      revision_updated_at: "2026-08-30T10:00:00Z",
      board_stage: "review",
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:00:00Z",
      drive_workspace: {
        provider: "google_drive",
        provisioning_status: "ready",
        folder_id: "f1",
        folder_web_url: "https://drive.google.com/drive/folders/f1",
        sheet_file_id: "s1",
        sheet_web_url: "https://docs.google.com/spreadsheets/d/s1",
        failure_category: null,
        attempt_count: 1,
        version: 1,
        retryable: false,
        lease_expires_at: null,
        requested_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:00:05Z",
      },
    },
    sales_opportunity_stage: "quoting",
    sales_opportunity_owner_key: "op@origenlab.cl",
    organization_display_name: "CEAF",
    contact_display_name: "Tatiana Rojas",
    contact_primary_email: "tatiana@ceaf.cl",
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

export function drivePendingQuoteItemFixture(
  overrides: Partial<DrivePendingQuoteItem> = {},
): DrivePendingQuoteItem {
  return {
    folder_id: "folder-1",
    folder_name: "CN01191-ICN Chile",
    folder_web_url: "https://drive.google.com/drive/folders/folder-1",
    document_identifier: "CN01191",
    created_time: "2026-08-01T12:00:00Z",
    modified_time: "2026-08-15T09:30:00Z",
    ...overrides,
  };
}
