/**
 * Strict parsers for durable customer-quote responses (CRM-Q1).
 *
 * Drive links pass through `safeDriveUrl`: only https URLs on the exact
 * drive.google.com / docs.google.com hosts survive; anything else becomes
 * null so the UI can never render an unvalidated link.
 */

import type {
  BoardStage,
  CustomerQuote,
  CustomerQuoteDrivePendingListResponse,
  CustomerQuoteDriveWorkspace,
  CustomerQuoteEvent,
  CustomerQuoteEventListResponse,
  CustomerQuoteGlobalItem,
  CustomerQuoteGlobalListResponse,
  CustomerQuoteListResponse,
  CustomerQuoteReadResponse,
  CustomerQuoteStatus,
  DrivePendingQuoteItem,
  QuoteOrigin,
  QuoteProvisioningStatus,
  RevisionStatus,
} from "./customerQuoteTypes";

const QUOTE_ID_RE = /^quote_[0-9a-f]{32}$/;
const SALES_OPPORTUNITY_ID_RE = /^sales_[0-9a-f]{32}$/;

const QUOTE_ORIGINS: ReadonlySet<string> = new Set(["generated", "adopted"]);

const REVISION_STATUSES: ReadonlySet<string> = new Set([
  "draft",
  "pending_approval",
  "adjustments_requested",
  "approved",
  "sent",
  "superseded",
]);

const BOARD_STAGES: ReadonlySet<string> = new Set([
  "preparation",
  "review",
  "approved_to_send",
  "sent_follow_up",
]);

const SALES_OPPORTUNITY_STAGES: ReadonlySet<string> = new Set([
  "new",
  "qualifying",
  "qualified",
  "quoting",
  "negotiating",
  "won",
  "lost",
  "dormant",
]);

const ALLOWED_DRIVE_HOSTS = new Set([
  "drive.google.com",
  "docs.google.com",
]);

const PROVISIONING_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "ready",
  "folder_ready",
  "failed",
]);

function record(
  raw: unknown,
  label: string,
): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`${label} must be an object`);
  }

  return raw as Record<string, unknown>;
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new Error(`${label} must be a string`);
  }

  return value;
}

function nullableString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }

  return stringValue(value, label);
}

function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${label} must be a boolean`);
  }

  return value;
}

function integerAtLeast(
  value: unknown,
  minimum: number,
  label: string,
): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum
  ) {
    throw new Error(`${label} must be an integer >= ${minimum}`);
  }

  return value;
}

export function safeDriveUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value) {
    return null;
  }

  let parsed: URL;

  try {
    parsed = new URL(value);
  } catch {
    return null;
  }

  if (parsed.protocol !== "https:") {
    return null;
  }

  if (!ALLOWED_DRIVE_HOSTS.has(parsed.hostname)) {
    return null;
  }

  return value;
}

function parseWorkspace(raw: unknown): CustomerQuoteDriveWorkspace {
  const data = record(raw, "drive_workspace");

  const provider = stringValue(data.provider, "drive_workspace.provider");

  if (provider !== "google_drive") {
    throw new Error(`Unknown drive workspace provider: ${provider}`);
  }

  const provisioningStatus = stringValue(
    data.provisioning_status,
    "drive_workspace.provisioning_status",
  );

  if (!PROVISIONING_STATUSES.has(provisioningStatus)) {
    throw new Error(
      `Unknown drive workspace provisioning status: ${provisioningStatus}`,
    );
  }

  return {
    provider,
    provisioning_status: provisioningStatus as QuoteProvisioningStatus,
    folder_id: nullableString(data.folder_id, "drive_workspace.folder_id"),
    folder_web_url: safeDriveUrl(data.folder_web_url),
    sheet_file_id: nullableString(
      data.sheet_file_id,
      "drive_workspace.sheet_file_id",
    ),
    sheet_web_url: safeDriveUrl(data.sheet_web_url),
    failure_category: nullableString(
      data.failure_category,
      "drive_workspace.failure_category",
    ),
    attempt_count: integerAtLeast(
      data.attempt_count,
      0,
      "drive_workspace.attempt_count",
    ),
    version: integerAtLeast(data.version, 1, "drive_workspace.version"),
    retryable: booleanValue(data.retryable, "drive_workspace.retryable"),
    lease_expires_at: nullableString(
      data.lease_expires_at,
      "drive_workspace.lease_expires_at",
    ),
    requested_at: nullableString(
      data.requested_at,
      "drive_workspace.requested_at",
    ),
    completed_at: nullableString(
      data.completed_at,
      "drive_workspace.completed_at",
    ),
  };
}

export function parseCustomerQuote(raw: unknown): CustomerQuote {
  const data = record(raw, "customer quote");

  const quoteId = stringValue(data.quote_id, "quote_id");

  if (!QUOTE_ID_RE.test(quoteId)) {
    throw new Error("quote_id has an unexpected format");
  }

  const salesOpportunityId = stringValue(
    data.sales_opportunity_id,
    "sales_opportunity_id",
  );

  if (!SALES_OPPORTUNITY_ID_RE.test(salesOpportunityId)) {
    throw new Error("sales_opportunity_id has an unexpected format");
  }

  const status = stringValue(data.status, "status");

  if (status !== "draft") {
    throw new Error(`Unknown customer quote status: ${status}`);
  }

  const quoteOrigin = stringValue(data.quote_origin, "quote_origin");

  if (!QUOTE_ORIGINS.has(quoteOrigin)) {
    throw new Error(`Unknown quote_origin: ${quoteOrigin}`);
  }

  const revisionStatus = stringValue(data.revision_status, "revision_status");

  if (!REVISION_STATUSES.has(revisionStatus)) {
    throw new Error(`Unknown revision_status: ${revisionStatus}`);
  }

  const boardStage = stringValue(data.board_stage, "board_stage");

  if (!BOARD_STAGES.has(boardStage)) {
    throw new Error(`Unknown board_stage: ${boardStage}`);
  }

  return {
    quote_id: quoteId,
    sales_opportunity_id: salesOpportunityId,
    quote_number: stringValue(data.quote_number, "quote_number"),
    document_number: stringValue(data.document_number, "document_number"),
    quote_origin: quoteOrigin as QuoteOrigin,
    sales_opportunity_title: stringValue(
      data.sales_opportunity_title,
      "sales_opportunity_title",
    ),
    status: status as CustomerQuoteStatus,
    version: integerAtLeast(data.version, 1, "version"),
    latest_revision_number: integerAtLeast(
      data.latest_revision_number,
      1,
      "latest_revision_number",
    ),
    revision_status: revisionStatus as RevisionStatus,
    revision_updated_by: stringValue(
      data.revision_updated_by,
      "revision_updated_by",
    ),
    revision_updated_at: stringValue(
      data.revision_updated_at,
      "revision_updated_at",
    ),
    board_stage: boardStage as BoardStage,
    created_by: stringValue(data.created_by, "created_by"),
    updated_by: stringValue(data.updated_by, "updated_by"),
    created_at: stringValue(data.created_at, "created_at"),
    updated_at: stringValue(data.updated_at, "updated_at"),
    drive_workspace: parseWorkspace(data.drive_workspace),
  };
}

export function parseCustomerQuoteListResponse(
  raw: unknown,
): CustomerQuoteListResponse {
  const data = record(raw, "customer quote list response");
  const meta = record(data.meta, "meta");

  if (!Array.isArray(data.items)) {
    throw new Error("items must be an array");
  }

  return {
    meta: {
      count: integerAtLeast(meta.count, 0, "meta.count"),
    },
    items: data.items.map(parseCustomerQuote),
  };
}

export function parseCustomerQuoteReadResponse(
  raw: unknown,
): CustomerQuoteReadResponse {
  const data = record(raw, "customer quote read response");

  return {
    item: parseCustomerQuote(data.item),
  };
}

function parseCustomerQuoteGlobalItem(raw: unknown): CustomerQuoteGlobalItem {
  const data = record(raw, "customer quote global item");

  const stage = stringValue(
    data.sales_opportunity_stage,
    "sales_opportunity_stage",
  );

  if (!SALES_OPPORTUNITY_STAGES.has(stage)) {
    throw new Error(`Unknown sales opportunity stage: ${stage}`);
  }

  return {
    quote: parseCustomerQuote(data.quote),
    sales_opportunity_stage:
      stage as CustomerQuoteGlobalItem["sales_opportunity_stage"],
    sales_opportunity_owner_key: stringValue(
      data.sales_opportunity_owner_key,
      "sales_opportunity_owner_key",
    ),
    organization_display_name: nullableString(
      data.organization_display_name,
      "organization_display_name",
    ),
    contact_display_name: nullableString(
      data.contact_display_name,
      "contact_display_name",
    ),
    contact_primary_email: nullableString(
      data.contact_primary_email,
      "contact_primary_email",
    ),
    next_task_title: nullableString(data.next_task_title, "next_task_title"),
    next_task_due_at: nullableString(
      data.next_task_due_at,
      "next_task_due_at",
    ),
  };
}

export function parseCustomerQuoteGlobalListResponse(
  raw: unknown,
): CustomerQuoteGlobalListResponse {
  const data = record(raw, "customer quote global list response");
  const meta = record(data.meta, "meta");

  if (!Array.isArray(data.items)) {
    throw new Error("items must be an array");
  }

  return {
    meta: {
      count: integerAtLeast(meta.count, 0, "meta.count"),
      total_count: integerAtLeast(meta.total_count, 0, "meta.total_count"),
      limit: integerAtLeast(meta.limit, 1, "meta.limit"),
      offset: integerAtLeast(meta.offset, 0, "meta.offset"),
    },
    items: data.items.map(parseCustomerQuoteGlobalItem),
  };
}

function parseDrivePendingQuoteItem(raw: unknown): DrivePendingQuoteItem {
  const data = record(raw, "drive pending quote item");

  return {
    folder_id: stringValue(data.folder_id, "folder_id"),
    folder_name: stringValue(data.folder_name, "folder_name"),
    folder_web_url: safeDriveUrl(data.folder_web_url),
    document_identifier: nullableString(
      data.document_identifier,
      "document_identifier",
    ),
    created_time: nullableString(data.created_time, "created_time"),
    modified_time: nullableString(data.modified_time, "modified_time"),
  };
}

function parseCustomerQuoteEvent(raw: unknown): CustomerQuoteEvent {
  const data = record(raw, "customer quote event");

  if (
    !data.payload ||
    typeof data.payload !== "object" ||
    Array.isArray(data.payload)
  ) {
    throw new Error("payload must be an object");
  }

  return {
    event_id: stringValue(data.event_id, "event_id"),
    event_type: stringValue(data.event_type, "event_type"),
    actor_key: stringValue(data.actor_key, "actor_key"),
    payload: data.payload as Record<string, unknown>,
    created_at: stringValue(data.created_at, "created_at"),
  };
}

export function parseCustomerQuoteEventListResponse(
  raw: unknown,
): CustomerQuoteEventListResponse {
  const data = record(raw, "customer quote event list response");
  const meta = record(data.meta, "meta");

  if (!Array.isArray(data.items)) {
    throw new Error("items must be an array");
  }

  return {
    meta: {
      count: integerAtLeast(meta.count, 0, "meta.count"),
    },
    items: data.items.map(parseCustomerQuoteEvent),
  };
}

export function parseCustomerQuoteDrivePendingListResponse(
  raw: unknown,
): CustomerQuoteDrivePendingListResponse {
  const data = record(raw, "drive pending quote list response");
  const meta = record(data.meta, "meta");

  if (!Array.isArray(data.items)) {
    throw new Error("items must be an array");
  }

  return {
    meta: {
      count: integerAtLeast(meta.count, 0, "meta.count"),
    },
    items: data.items.map(parseDrivePendingQuoteItem),
  };
}
