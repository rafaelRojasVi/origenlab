/**
 * Client for GET dashboard.origenlab.cl/api/stats/web-stats.
 *
 * The route is handled directly by the Worker (apps/dashboard-proxy/src/stats_read.ts),
 * NOT proxied to the API — the CF Analytics Engine SQL API credentials live in the Worker.
 * The browser uses `credentials: "include"` so the session cookie is sent, and the Worker
 * validates it before querying the AE SQL API.
 *
 * The URL is relative (no VITE_ORIGENLAB_API_BASE_URL prefix) because the Worker handles
 * this path on the same origin as the dashboard (dashboard.origenlab.cl/api/stats/web-stats).
 */

import { OperatorApiError } from "../../api/operatorClient";

/** Per-window aggregate returned by the Worker. */
export interface WebStatsPeriod {
  /** Acquisition visits (all origenes except «interno»). */
  visitas_total: number;
  /** Same-site navigation visits, reported separately. */
  visitas_interno: number;
  /** Visit count by acquisition origen. */
  visitas_by_origen: Record<string, number>;
  whatsapp_total: number;
  /** [modelo, count] pairs, most clicked first. */
  whatsapp_top_models: [string, number][];
  cotizacion_total: number;
  cotizacion_top_models: [string, number][];
  form_submits: number;
  pdf_downloads: number;
  d3_interacts: number;
  /** [pagina, count] pairs, most visited first (all origenes). */
  top_pages: [string, number][];
}

export interface WebStatsData {
  "7d": WebStatsPeriod;
  "30d": WebStatsPeriod;
  fetched_at: string;
}

export type WebStatsWindow = "7d" | "30d";

/** The Worker returns this error code when CF secrets are not configured. */
export const NOT_CONFIGURED_CODE = "not_configured";

export async function fetchWebStats(): Promise<WebStatsData> {
  const res = await fetch("/api/stats/web-stats", {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<WebStatsData>;
}
