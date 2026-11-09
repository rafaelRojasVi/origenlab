/**
 * Dashboard read route: GET dashboard.origenlab.cl/api/stats/web-stats
 *
 * Reads the `origenlab_web_stats` Analytics Engine dataset via the Cloudflare SQL API and
 * returns aggregated totals for the last 7 and 30 days. Requires two Worker secrets:
 *   CF_ACCOUNT_ID       — the Cloudflare account that owns the AE dataset
 *   CF_STATS_API_TOKEN  — an API token with «Account Analytics: Read»
 *
 * When either secret is absent the route answers 503 `not_configured` and the dashboard
 * shows a configuration notice instead of an error.
 *
 * Session validation: a HEAD /auth/session subrequest to the API (same session cookie,
 * same API token and CF Access headers the Worker sends for every other dashboard read).
 * Any result other than 200 is forwarded unchanged to the browser.
 *
 * Caching: `caches.default` for 5 minutes under a canonical internal key so the dashboard
 * does not hammer the SQL API; the cache entry is invalidated if the account id changes.
 *
 * Data layout (src/stats.ts `statsDataPoint`):
 *   blob1 — event name  (cotizacion_click | whatsapp_click | pdf_download | 3d_interact | form_submit | visita)
 *   blob2 — modelo slug (non-empty for click/download events, "" for visita)
 *   blob3 — origen      (non-empty for visita, "" for others; closed set in STATS_ORIGENES)
 *   blob4 — pagina path (non-empty for visita, "" for others)
 *   double1 — 1 (always; use SUM(_sample_interval) for counts)
 *
 * «interno» visits (same-site navigation) are excluded from the acquisition totals and
 * reported separately in `visitas_interno` so they don't inflate the marketing funnel.
 */

import { AUTH_SESSION_PATH } from "./auth";
import {
  API_AUTH_HEADER,
  CF_ACCESS_CLIENT_ID_HEADER,
  CF_ACCESS_CLIENT_SECRET_HEADER,
  type ProxyEnv,
} from "./proxy";

/** Additional secrets required for reading stats. Added as wrangler secrets (never vars). */
export interface StatsReadSecrets {
  /** The Cloudflare account id that owns the `origenlab_web_stats` AE dataset. */
  CF_ACCOUNT_ID?: string;
  /** An API token with the «Account Analytics: Read» permission for that account. */
  CF_STATS_API_TOKEN?: string;
}

export type StatsReadEnv = ProxyEnv & StatsReadSecrets;

/** Per-window aggregate returned to the dashboard. */
export interface WebStatsPeriod {
  /** Acquisition visits (all origenes except «interno»). */
  visitas_total: number;
  /** Same-site navigation visits, reported separately and not in `visitas_total`. */
  visitas_interno: number;
  /** Visit count per acquisition origen. «interno» is excluded. */
  visitas_by_origen: Record<string, number>;
  whatsapp_total: number;
  whatsapp_top_models: [string, number][];
  cotizacion_total: number;
  cotizacion_top_models: [string, number][];
  form_submits: number;
  pdf_downloads: number;
  d3_interacts: number;
  /** Top pages by visit count (all origenes including interno). */
  top_pages: [string, number][];
}

export interface WebStatsResponse {
  "7d": WebStatsPeriod;
  "30d": WebStatsPeriod;
  fetched_at: string;
}

const CF_AE_SQL_BASE = "https://api.cloudflare.com/client/v4/accounts";
const CACHE_TTL_SECONDS = 300; // 5 minutes
/** How many modelo/page entries to include in «top» lists. */
const TOP_N = 5;

/** Build the SQL query for one time window. */
function buildQuery(days: 7 | 30): string {
  return `SELECT blob1 AS event_name, blob2 AS modelo, blob3 AS origen, blob4 AS pagina, SUM(_sample_interval) AS cnt FROM origenlab_web_stats WHERE timestamp >= NOW() - INTERVAL '${days}' DAY GROUP BY event_name, modelo, origen, pagina ORDER BY cnt DESC LIMIT 500`;
}

interface AeRow {
  event_name: string;
  modelo: string;
  origen: string;
  pagina: string;
  cnt: number;
}

interface AeResponse {
  data?: AeRow[];
}

/** Convert one window's raw AE rows into the structured `WebStatsPeriod`. */
function buildPeriod(rows: AeRow[]): WebStatsPeriod {
  let visitas_total = 0;
  let visitas_interno = 0;
  let whatsapp_total = 0;
  let cotizacion_total = 0;
  let form_submits = 0;
  let pdf_downloads = 0;
  let d3_interacts = 0;
  const visitas_by_origen: Record<string, number> = {};
  const whatsapp_models: Record<string, number> = {};
  const cotizacion_models: Record<string, number> = {};
  const pages: Record<string, number> = {};

  for (const row of rows) {
    const cnt = Number(row.cnt) || 0;
    switch (row.event_name) {
      case "visita":
        if (row.origen === "interno") {
          visitas_interno += cnt;
        } else {
          visitas_total += cnt;
          if (row.origen) {
            visitas_by_origen[row.origen] = (visitas_by_origen[row.origen] ?? 0) + cnt;
          }
        }
        if (row.pagina) {
          pages[row.pagina] = (pages[row.pagina] ?? 0) + cnt;
        }
        break;
      case "whatsapp_click":
        whatsapp_total += cnt;
        if (row.modelo) {
          whatsapp_models[row.modelo] = (whatsapp_models[row.modelo] ?? 0) + cnt;
        }
        break;
      case "cotizacion_click":
        cotizacion_total += cnt;
        if (row.modelo) {
          cotizacion_models[row.modelo] = (cotizacion_models[row.modelo] ?? 0) + cnt;
        }
        break;
      case "form_submit":
        form_submits += cnt;
        break;
      case "pdf_download":
        pdf_downloads += cnt;
        break;
      case "3d_interact":
        d3_interacts += cnt;
        break;
    }
  }

  const topN = (record: Record<string, number>): [string, number][] =>
    Object.entries(record)
      .sort((a, b) => b[1] - a[1])
      .slice(0, TOP_N);

  return {
    visitas_total,
    visitas_interno,
    visitas_by_origen,
    whatsapp_total,
    whatsapp_top_models: topN(whatsapp_models),
    cotizacion_total,
    cotizacion_top_models: topN(cotizacion_models),
    form_submits,
    pdf_downloads,
    d3_interacts,
    top_pages: topN(pages),
  };
}

/** Query the CF Analytics Engine SQL API for one time window. Returns rows or throws. */
async function queryAe(accountId: string, apiToken: string, days: 7 | 30): Promise<AeRow[]> {
  const url = `${CF_AE_SQL_BASE}/${encodeURIComponent(accountId)}/analytics_engine/sql`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query: buildQuery(days) }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`CF AE SQL API ${res.status}: ${text.slice(0, 200)}`);
  }
  const json = (await res.json()) as AeResponse;
  return json.data ?? [];
}

function jsonResp(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store, private",
    },
  });
}

/** Validate the browser's session by pinging the API's /auth/session endpoint. */
async function validateSession(request: Request, env: StatsReadEnv): Promise<number> {
  const upstream = env.ORIGENLAB_API_UPSTREAM?.trim().replace(/\/$/, "");
  if (!upstream) return 502;

  // Forward only the two sign-in cookies (mirrors filterAuthCookieHeader in auth.ts).
  const cookieHeader = request.headers.get("Cookie") ?? "";
  const SESSION_NAMES = ["__Host-origenlab_session", "origenlab_session"];
  const kept = cookieHeader
    .split(";")
    .map((p) => p.trim())
    .filter((p) => SESSION_NAMES.some((n) => p.startsWith(`${n}=`)));
  if (kept.length === 0) return 401;

  const headers = new Headers({
    Cookie: kept.join("; "),
    [API_AUTH_HEADER]: env.ORIGENLAB_API_AUTH_TOKEN ?? "",
  });
  const cfId = env.CF_ACCESS_CLIENT_ID?.trim();
  const cfSecret = env.CF_ACCESS_CLIENT_SECRET?.trim();
  if (cfId && cfSecret) {
    headers.set(CF_ACCESS_CLIENT_ID_HEADER, cfId);
    headers.set(CF_ACCESS_CLIENT_SECRET_HEADER, cfSecret);
  }

  try {
    const res = await fetch(`${upstream}${AUTH_SESSION_PATH}`, {
      method: "HEAD",
      headers,
    });
    return res.status;
  } catch {
    return 502;
  }
}

/** Cache key: canonical URL that encodes the account id so a rotation busts the cache. */
function cacheKey(accountId: string): Request {
  return new Request(`https://internal.origenlab-stats/web-stats?a=${encodeURIComponent(accountId)}`);
}

export async function handleWebStatsRequest(
  request: Request,
  env: StatsReadEnv,
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return jsonResp({ error: { code: "method_not_allowed" } }, 405);
  }

  const accountId = env.CF_ACCOUNT_ID?.trim() ?? "";
  const apiToken = env.CF_STATS_API_TOKEN?.trim() ?? "";
  if (!accountId || !apiToken) {
    return jsonResp({ error: { code: "not_configured" } }, 503);
  }

  // Validate the dashboard session before reading analytics.
  const sessionStatus = await validateSession(request, env);
  if (sessionStatus === 401) return jsonResp({ error: { code: "unauthorized" } }, 401);
  if (sessionStatus === 403) return jsonResp({ error: { code: "forbidden" } }, 403);
  if (sessionStatus >= 500) return jsonResp({ error: { code: "upstream_error" } }, 502);

  // HEAD only needs auth confirmation; skip the AE query.
  if (request.method === "HEAD") {
    return new Response(null, {
      status: 200,
      headers: { "Cache-Control": "no-store, private" },
    });
  }

  // Serve from cache if fresh.
  const key = cacheKey(accountId);
  try {
    const cache = await caches.open("web-stats-read");
    const hit = await cache.match(key);
    if (hit) return hit.clone();
  } catch {
    // Cache unavailable — proceed to fetch.
  }

  // Query the CF AE SQL API for both windows in parallel.
  let rows7d: AeRow[];
  let rows30d: AeRow[];
  try {
    [rows7d, rows30d] = await Promise.all([queryAe(accountId, apiToken, 7), queryAe(accountId, apiToken, 30)]);
  } catch (err) {
    console.error("web_stats_ae_error", String(err));
    return jsonResp({ error: { code: "ae_query_failed" } }, 502);
  }

  const payload: WebStatsResponse = {
    "7d": buildPeriod(rows7d),
    "30d": buildPeriod(rows30d),
    fetched_at: new Date().toISOString(),
  };

  const responseBody = JSON.stringify(payload);
  const response = new Response(responseBody, {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      // Private to the dashboard session; the CDN must not share it across users.
      "Cache-Control": "private, max-age=0",
    },
  });

  // Store in the Worker cache (server-side, not shared with the browser).
  try {
    const toCache = new Response(responseBody, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": `public, max-age=${CACHE_TTL_SECONDS}`,
      },
    });
    const cache = await caches.open("web-stats-read");
    await cache.put(key, toCache);
  } catch {
    // Cache write failure is non-fatal.
  }

  return response;
}
