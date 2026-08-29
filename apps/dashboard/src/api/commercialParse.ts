/**
 * Defensive parsers for commercial API responses.
 * Strips sensitive fields and tolerates null / missing optional values.
 */

import type {
  EquipmentAnexoItem,
  EquipmentOpportunityItem,
  EquipmentOpportunitiesMeta,
  EquipmentOpportunitiesUiResponse,
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

const EQUIPMENT_DETAIL_OPTIONAL_FIELDS = [
  "fecha_publicacion",
  "close_at",
  "validity_status",
  "chilecompra_status",
  "chilecompra_status_code",
  "api_checked_at_utc",
  "source",
  "mercado_publico_url",
  "title",
  "unspsc_code",
  "unidad",
  "cantidad",
  "producto",
  "nivel_1",
  "nivel_2",
  "nivel_3",
] as const;

function optionalEquipmentField(
  r: Record<string, unknown>,
  field: (typeof EQUIPMENT_DETAIL_OPTIONAL_FIELDS)[number],
  maxLen: number,
): string | undefined {
  const value = safePreviewText(r[field], maxLen);
  return value || undefined;
}

function normalizeAnexoItem(raw: unknown): EquipmentAnexoItem | null {
  const item = asRecord(raw);
  const nombre = safePreviewText(item.nombre, 200);
  const tipo = safePreviewText(item.tipo, 80);
  const descripcion = safePreviewText(item.descripcion, 300);
  const tamano = safePreviewText(item.tamano, 40);
  const fecha_adjunto = safePreviewText(item.fecha_adjunto, 40);
  const url = safePreviewText(item.url, 300);
  if (!nombre && !tipo && !descripcion && !tamano && !fecha_adjunto && !url) {
    return null;
  }
  const anexo: EquipmentAnexoItem = {};
  if (nombre) anexo.nombre = nombre;
  if (tipo) anexo.tipo = tipo;
  if (descripcion) anexo.descripcion = descripcion;
  if (tamano) anexo.tamano = tamano;
  if (fecha_adjunto) anexo.fecha_adjunto = fecha_adjunto;
  if (url && !/ticket|api\.chilecompra/i.test(url)) {
    anexo.url = url;
  }
  return anexo;
}

function parseEquipmentAnexos(raw: unknown): EquipmentAnexoItem[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const items = raw
    .map((entry) => normalizeAnexoItem(entry))
    .filter((entry): entry is EquipmentAnexoItem => entry !== null);
  return items.length > 0 ? items : undefined;
}

export function normalizeEquipmentItem(raw: unknown, index: number): EquipmentOpportunityItem {
  const r = asRecord(raw);
  const rank =
    typeof r.priority_rank === "number" && Number.isFinite(r.priority_rank)
      ? r.priority_rank
      : index + 1;
  const item: EquipmentOpportunityItem = {
    priority_rank: rank,
    codigo_licitacion: safePreviewText(r.codigo_licitacion, 80),
    buyer: safePreviewText(r.buyer, 200),
    region: safePreviewText(r.region, 80),
    close_date: safePreviewText(r.close_date, 40),
    equipment_category: safePreviewText(r.equipment_category, 120),
    item_description: safePreviewText(r.item_description, 400),
    next_action: safePreviewText(r.next_action, 120),
    safe_channel: safePreviewText(r.safe_channel, 80),
    supplier_needed: safePreviewText(r.supplier_needed, 40),
    contact_status: safePreviewText(r.contact_status, 120),
    contact_email: safePreviewText(r.contact_email, 200),
    operator_note: safePreviewText(r.operator_note, 200),
  };
  const opportunityKey = safePreviewText(r.opportunity_key, 160);
  if (opportunityKey) {
    item.opportunity_key = opportunityKey;
  }
  for (const field of EQUIPMENT_DETAIL_OPTIONAL_FIELDS) {
    const maxLen = field === "mercado_publico_url" ? 300 : field === "title" ? 200 : 120;
    const value = optionalEquipmentField(r, field, maxLen);
    if (value) {
      item[field] = value;
    }
  }
  const anexos = parseEquipmentAnexos(r.anexos);
  if (anexos) {
    item.anexos = anexos;
  }
  return item;
}

/** UI-safe meta: source_path and other filesystem hints are not exposed. */
export function parseEquipmentMeta(raw: unknown): Omit<EquipmentOpportunitiesMeta, "source_path"> {
  const m = asRecord(raw);
  const dataSource = safeStr(m.data_source);
  return {
    data_source: dataSource === "postgres_mirror" ? "postgres_mirror" : "active_current_csv",
    read_only: m.read_only !== false,
    count: typeof m.count === "number" && Number.isFinite(m.count) ? m.count : 0,
    campaign_mode:
      m.campaign_mode === null || m.campaign_mode === undefined
        ? null
        : safeStr(m.campaign_mode) || null,
    reduced_mode: Boolean(m.reduced_mode),
    note: safePreviewText(m.note, 500),
  };
}

export function parseEquipmentOpportunitiesResponse(
  data: unknown,
): EquipmentOpportunitiesUiResponse {
  const row = asRecord(data);
  const itemsRaw = Array.isArray(row.items) ? row.items : [];
  const items = itemsRaw.map((item, index) => normalizeEquipmentItem(item, index));
  const meta = parseEquipmentMeta(row.meta);
  return {
    meta: { ...meta, count: meta.count || items.length },
    items,
  };
}
