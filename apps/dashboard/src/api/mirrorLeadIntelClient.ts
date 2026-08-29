import {
  parseLeadProspectDetailResponse,
  parseLeadProspectsListResponse,
  parseLeadResearchSummaryResponse,
} from "./leadIntelParse";
import type {
  LeadProspectDetailResponseUi,
  LeadProspectsExportQuery,
  LeadProspectsListQuery,
  LeadProspectsListUi,
  LeadResearchSummaryUi,
} from "./leadIntelTypes";
import { OperatorApiError, fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const MIRROR_LEADS_PROSPECTS_PATH = "/mirror/leads/prospects";
export const MIRROR_LEADS_PROSPECTS_EXPORT_PATH = "/mirror/leads/prospects/export.csv";
export const MIRROR_LEADS_SUMMARY_PATH = "/mirror/leads/summary";

function leadProspectsQueryParams(
  query: LeadProspectsListQuery,
): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    limit: query.limit ?? 100,
    include_blocked: query.include_blocked ?? false,
  };
  if (query.q?.trim()) params.q = query.q.trim();
  if (query.classification?.trim()) params.classification = query.classification.trim();
  if (query.source_type?.trim()) params.source_type = query.source_type.trim();
  if (query.blocked_only) params.blocked_only = true;
  if (query.sector?.trim()) params.sector = query.sector.trim();
  if (query.region?.trim()) params.region = query.region.trim();
  if (query.buyer_type?.trim()) params.buyer_type = query.buyer_type.trim();
  if (query.campaign_bucket?.trim()) params.campaign_bucket = query.campaign_bucket.trim();
  if (query.min_score != null) params.min_score = query.min_score;
  if (query.contact_scope?.trim()) params.contact_scope = query.contact_scope.trim();
  return params;
}

export function mirrorLeadProspectsUrl(query: LeadProspectsListQuery = {}): string {
  return operatorApiUrl(MIRROR_LEADS_PROSPECTS_PATH, leadProspectsQueryParams(query));
}

export function mirrorLeadProspectsExportUrl(query: LeadProspectsExportQuery): string {
  const params = leadProspectsQueryParams(query);
  params.export_queue = query.export_queue;
  if (query.commercial_action_bucket?.trim()) {
    params.commercial_action_bucket = query.commercial_action_bucket.trim();
  }
  return operatorApiUrl(MIRROR_LEADS_PROSPECTS_EXPORT_PATH, params);
}

export async function downloadLeadProspectsExportCsv(query: LeadProspectsExportQuery): Promise<void> {
  const url = mirrorLeadProspectsExportUrl(query);
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? "prospectos-export.csv";
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

export function mirrorLeadProspectDetailUrl(prospectKey: string): string {
  return operatorApiUrl(`${MIRROR_LEADS_PROSPECTS_PATH}/${encodeURIComponent(prospectKey.trim())}`);
}

export function fetchLeadProspectsMirror(
  query: LeadProspectsListQuery = {},
): Promise<LeadProspectsListUi> {
  return fetchJsonGet<unknown>(mirrorLeadProspectsUrl(query)).then(parseLeadProspectsListResponse);
}

export function fetchLeadProspectDetailMirror(
  prospectKey: string,
): Promise<LeadProspectDetailResponseUi> {
  return fetchJsonGet<unknown>(mirrorLeadProspectDetailUrl(prospectKey)).then(
    parseLeadProspectDetailResponse,
  );
}

export function fetchLeadResearchSummaryMirror(): Promise<LeadResearchSummaryUi> {
  return fetchJsonGet<unknown>(operatorApiUrl(MIRROR_LEADS_SUMMARY_PATH)).then(
    parseLeadResearchSummaryResponse,
  );
}
