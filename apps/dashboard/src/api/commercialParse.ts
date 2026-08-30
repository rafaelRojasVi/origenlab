/**
 * Defensive parsers for commercial API responses.
 * Strips sensitive fields and tolerates null / missing optional values.
 */

import type {
  WarmCaseCategory,
  WarmCaseItem,
  WarmCaseStatus,
  WarmCasesMeta,
  WarmCasesResponse,
} from "./commercialTypes";
import { safePreviewText, safeStr } from "../lib/safeText";

const WARM_CATEGORIES = new Set<string>([
  "client_opportunity",
  "client_response",
  "supplier_quote_received",
  "supplier_followup",
  "payment_admin",
  "logistics_admin",
  "internal_admin",
  "system_noise",
  "bounce_problem",
  "deal_evidence_candidate",
  "quote_sent",
  "waiting_supplier",
  "waiting_client",
  "campaign_outreach",
  "waiting_campaign_reply",
  "auto_acknowledgement",
  "client_reply",
  "supplier_reply",
  "bounce",
  "opportunity",
  "auto_reply",
  "vendor_logistics",
]);

const WARM_STATUSES = new Set<string>(["new", "open", "waiting", "quoted", "problem"]);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function normalizeWarmCategory(value: unknown): WarmCaseCategory {
  const cat = safeStr(value).toLowerCase();
  if (WARM_CATEGORIES.has(cat)) {
    return cat as WarmCaseCategory;
  }
  return "opportunity";
}

function normalizeWarmStatus(value: unknown): WarmCaseStatus {
  const st = safeStr(value).toLowerCase();
  if (WARM_STATUSES.has(st)) {
    return st as WarmCaseStatus;
  }
  return "open";
}

export function normalizeWarmCaseItem(raw: unknown, index: number): WarmCaseItem {
  const r = asRecord(raw);
  return {
    case_id: safeStr(r.case_id) || `warm-row-${index + 1}`,
    last_email_id: typeof r.last_email_id === "number" && Number.isFinite(r.last_email_id)
      ? r.last_email_id
      : 0,
    last_seen_at:
      r.last_seen_at === null || r.last_seen_at === undefined
        ? null
        : safeStr(r.last_seen_at) || null,
    account_name: safePreviewText(r.account_name, 200),
    contact_email: safeStr(r.contact_email),
    subject: safePreviewText(r.subject, 300),
    category: normalizeWarmCategory(r.category),
    status: normalizeWarmStatus(r.status),
    next_action: safePreviewText(r.next_action, 200),
    equipment_signal: safePreviewText(r.equipment_signal, 120),
    snippet: safePreviewText(r.snippet, 400),
    gmail_url: null,
    grouped_email_count:
      typeof r.grouped_email_count === "number" && Number.isFinite(r.grouped_email_count)
        ? Math.max(1, Math.floor(r.grouped_email_count))
        : 1,
  };
}

export function parseWarmCasesMeta(raw: unknown): WarmCasesMeta {
  const m = asRecord(raw);
  const dataSource = safeStr(m.data_source);
  return {
    data_source: dataSource === "postgres_mirror" ? "postgres_mirror" : "sqlite",
    read_only: m.read_only !== false,
    reduced_mode: Boolean(m.reduced_mode),
    count: typeof m.count === "number" && Number.isFinite(m.count) ? m.count : 0,
    enrichment_available: Boolean(m.enrichment_available),
    note: safePreviewText(m.note, 500),
  };
}

export function parseWarmCasesResponse(data: unknown): WarmCasesResponse {
  const row = asRecord(data);
  const itemsRaw = Array.isArray(row.items) ? row.items : [];
  const items = itemsRaw.map((item, index) => normalizeWarmCaseItem(item, index));
  const meta = parseWarmCasesMeta(row.meta);
  return {
    meta: { ...meta, count: meta.count || items.length },
    items,
  };
}
