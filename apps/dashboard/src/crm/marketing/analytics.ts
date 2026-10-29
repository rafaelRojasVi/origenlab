/**
 * Marketing analytics: the figures the CRM measures (`apps/api` v2/marketing_analytics.py) and
 * the people behind each one. Two GET reads; `null` is "not measurable", never zero.
 */

import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";

export type CampaignMetric =
  | "sent" | "new_people" | "bounced" | "baja" | "responded" | "quote_request" | "cases_after" | "ndr_unblocked";
export type OverviewMetric = "reached" | "writable" | "bounced" | "baja" | "with_open_case";
export type Segment = "sin_caso" | "solicitada" | "enviada" | "conversacion" | "pausa" | "ganada" | "perdida";
export type CoverageColumn = "contacted" | "not_contacted" | "blocked";

export interface CoverageRow {
  segment: Segment;
  people: number;
  contacted: number;
  not_contacted: number;
  blocked: number;
}

export interface MarketingAnalytics {
  reply_window_days: number;
  overview: Record<OverviewMetric, number> & { other_blocks: number };
  campaigns: Record<string, Record<CampaignMetric, number | null>>;
  coverage: CoverageRow[];
  alerts: { ndr_unblocked: number };
  mail_captured_from: string | null;
}

export interface PeopleRow {
  address_norm: string;
  opportunity_id: string | null;
  person_id: string | null;
  name: string | null;
  organization: string | null;
  case_stage: string | null;
  case_title: string | null;
}

export interface PeoplePage {
  total: number;
  rows: PeopleRow[];
  limit: number;
  offset: number;
}

/** What a people list is for: one campaign figure, one overview figure, or one coverage cell. */
export type PeopleQuery =
  | { kind: "campaign"; metric: CampaignMetric; campaignId: string }
  | { kind: "overview"; metric: OverviewMetric }
  | { kind: "coverage"; segment: Segment | "total"; column?: CoverageColumn };

export const ANALYTICS_PATHS = {
  analytics: "/v2/workspace/marketing/analytics",
  people: "/v2/workspace/marketing/analytics/people",
} as const;

export const fetchMarketingAnalytics = () => fetchJsonGet<MarketingAnalytics>(operatorApiUrl(ANALYTICS_PATHS.analytics));

export function peopleSearch(q: PeopleQuery, offset = 0): string {
  const p = new URLSearchParams();
  if (q.kind === "coverage") {
    p.set("segment", q.segment);
    if (q.column) p.set("column", q.column);
  } else {
    p.set("metric", q.metric);
    if (q.kind === "campaign") p.set("campaign_id", q.campaignId);
  }
  p.set("limit", "50");
  p.set("offset", String(offset));
  return p.toString();
}

export const fetchMarketingPeople = (q: PeopleQuery, offset = 0) =>
  fetchJsonGet<PeoplePage>(`${operatorApiUrl(ANALYTICS_PATHS.people)}?${peopleSearch(q, offset)}`);

export const SEGMENT_LABEL: Record<Segment, string> = {
  sin_caso: "Sin caso nunca",
  solicitada: "Solicitada / En estudio",
  enviada: "Enviada",
  conversacion: "Conversación",
  pausa: "En pausa",
  ganada: "Ganada (cliente)",
  perdida: "Perdida",
};

export const COLUMN_LABEL: Record<CoverageColumn, string> = {
  contacted: "Contactadas",
  not_contacted: "Sin contactar",
  blocked: "Bloqueadas",
};

export const CAMPAIGN_METRIC_LABEL: Record<CampaignMetric, string> = {
  sent: "Enviados",
  new_people: "Personas nuevas",
  bounced: "Rebotes",
  baja: "Bajas",
  responded: "Respondieron",
  quote_request: "Pidieron cotización",
  cases_after: "Casos después",
  ndr_unblocked: "Rebotes sin bloquear",
};

export const OVERVIEW_LABEL: Record<OverviewMetric, string> = {
  writable: "Aún se les puede escribir",
  reached: "Alcanzadas",
  bounced: "Rebotes",
  baja: "Bajas",
  with_open_case: "Con caso abierto",
};

export const CASE_STAGE_LABEL: Record<string, string> = {
  lead: "Solicitada",
  qualifying: "Solicitada",
  qualified: "Solicitada",
  quoting: "Enviada",
  negotiating: "Conversación",
  won: "Ganada",
  lost: "Perdida",
  abandoned: "Cerrada",
};

/** `12,3 %` of a whole, or null when there is no whole. */
export function pct(part: number | null | undefined, whole: number | null | undefined): string | null {
  if (part == null || !whole) return null;
  return `${(Math.round((part / whole) * 1000) / 10).toLocaleString("es-CL")} %`;
}
