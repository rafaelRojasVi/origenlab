import type { ProxyEnv } from "./proxy";

/**
 * First-party analytics under `origenlab.cl/api/stats/*`.
 *
 * The public site's CSP is `connect-src 'self'`, so every analytics call stays on origenlab.cl.
 * This Worker route family is public (like `/api/web/*`), has no session, no operator identity
 * and no CORS, and is separate from the dashboard allowlist. Exactly one request exists:
 *
 *   POST /api/stats/event      one of six named events, `{name, props}`: the five click events take
 *                              `props:{modelo?}`, `visita` takes `props:{origen, pagina}`
 *
 * The Worker records the event itself as one Workers Analytics Engine data point (binding
 * `STATS_AE`, dataset `origenlab_web_stats`) and answers 204. Totals are read with the Analytics
 * Engine SQL API (README). No read-before-write, so concurrent events never lose a count; without
 * the binding the event is validated and answered 204, and nothing is written.
 *
 * Nothing here logs an IP, a User-Agent or a body, and nothing is forwarded anywhere.
 */

export const STATS_BODY_LIMIT = 2_048;
export const STATS_EVENT_NAMES: ReadonlySet<string> = new Set([
  "cotizacion_click",
  "whatsapp_click",
  "pdf_download",
  "3d_interact",
  "form_submit",
  "visita",
]);
/** Where a visit came from, decided by the site; a closed set so a client cannot mint keys. */
export const STATS_ORIGENES: ReadonlySet<string> = new Set([
  "google",
  "perfil-google",
  "linkedin",
  "directo",
  "whatsapp",
  "otro",
  // Same-site navigation: the visitor came from another origenlab.cl page.
  "interno",
]);

const STATS_SITE_ORIGIN = "https://origenlab.cl";
const EVENT_PATH = "/api/stats/event";
const PAGINA_RE = /^(?!.*\/\/)\/[a-z0-9/-]{0,80}$/;
const MODELO_RE = /^[A-Za-z0-9._-]{1,64}$/;

interface RateLimit {
  limit(opts: { key: string }): Promise<{ success: boolean }>;
}

export type StatsEnv = ProxyEnv & {
  /** Comma-separated modelo slugs the counter may key on; anything else counts as "other". */
  STATS_MODELOS?: string;
  /** Comma-separated page paths a visit counter may key on; any other page counts as "otra". */
  STATS_PAGINAS?: string;
  STATS_RATELIMIT?: RateLimit;
  /** Workers Analytics Engine dataset `origenlab_web_stats` (counter mode). Optional: absent = no write. */
  STATS_AE?: AnalyticsEngineDataset;
};

export interface StatsEvent {
  name: string;
  modelo: string | undefined;
  origen: string | undefined;
  pagina: string | undefined;
}

const empty = (status: number, headers?: Record<string, string>): Response =>
  new Response(null, { status, headers: { "Cache-Control": "no-store", ...headers } });

function parseEvent(buf: ArrayBuffer): StatsEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(new TextDecoder().decode(buf));
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const { name, props } = data as { name?: unknown; props?: unknown };
  if (typeof name !== "string" || !STATS_EVENT_NAMES.has(name)) return null;

  if (props !== undefined && props !== null && (typeof props !== "object" || Array.isArray(props))) return null;
  const p = (props ?? {}) as { modelo?: unknown; origen?: unknown; pagina?: unknown };

  if (name === "visita") {
    // A visit carries exactly a source and a page; no modelo, nothing else is kept.
    if (p.modelo !== undefined) return null;
    if (typeof p.origen !== "string" || !STATS_ORIGENES.has(p.origen)) return null;
    if (typeof p.pagina !== "string" || !PAGINA_RE.test(p.pagina)) return null;
    return { name, modelo: undefined, origen: p.origen, pagina: p.pagina };
  }

  // The five click events keep their contract: only `modelo`, never origen/pagina.
  if (p.origen !== undefined || p.pagina !== undefined) return null;
  let modelo: string | undefined;
  if (p.modelo !== undefined) {
    if (typeof p.modelo !== "string" || !MODELO_RE.test(p.modelo)) return null;
    modelo = p.modelo;
  }
  return { name, modelo, origen: undefined, pagina: undefined };
}

const OTHER_MODELO = "other";

function allowedModelos(raw: string | undefined): Set<string> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((slug) => slug.trim())
      .filter((slug) => MODELO_RE.test(slug)),
  );
}

const OTHER_PAGINA = "otra";

function allowedPaginas(raw: string | undefined): Set<string> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((path) => path.trim())
      .filter((path) => PAGINA_RE.test(path)),
  );
}

/**
 * The Analytics Engine data point of one event:
 *   blobs   [name, modelo | "", origen | "", pagina | ""]
 *   doubles [1]
 *   indexes [name]
 * Cardinality stays bounded: a modelo outside `STATS_MODELOS` is recorded as
 * "other", a page outside `STATS_PAGINAS` as "otra", and origen is a closed set of seven.
 */
export function statsDataPoint(event: StatsEvent, env: StatsEnv): AnalyticsEngineDataPoint {
  let modelo = "";
  let origen = "";
  let pagina = "";
  if (event.name === "visita" && event.origen !== undefined && event.pagina !== undefined) {
    origen = event.origen;
    pagina = allowedPaginas(env.STATS_PAGINAS).has(event.pagina) ? event.pagina : OTHER_PAGINA;
  } else if (event.modelo !== undefined) {
    modelo = allowedModelos(env.STATS_MODELOS).has(event.modelo) ? event.modelo : OTHER_MODELO;
  }
  return { blobs: [event.name, modelo, origen, pagina], doubles: [1], indexes: [event.name] };
}

/** One write per event, fire-and-forget; counting never blocks or fails the visitor's request. */
function recordEvent(dataset: AnalyticsEngineDataset, event: StatsEvent, env: StatsEnv): void {
  try {
    dataset.writeDataPoint(statsDataPoint(event, env));
  } catch {
    console.warn("stats_write_failed"); // a fixed string: no event, no IP, no body
  }
}

export async function handleStatsRequest(request: Request, env: StatsEnv): Promise<Response> {
  const url = new URL(request.url);
  // Only one method+path pair exists; everything else is indistinguishable from a missing route.
  if (url.pathname !== EVENT_PATH || request.method !== "POST") return empty(404);

  if (request.headers.get("Origin") !== STATS_SITE_ORIGIN) return empty(403);
  const site = request.headers.get("Sec-Fetch-Site");
  if (site !== null && site !== "same-origin" && site !== "same-site") return empty(403);
  const contentType = (request.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  // text/plain is what sendBeacon sends for a string body; both carry the same JSON.
  if (contentType !== "application/json" && contentType !== "text/plain") return empty(415);
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!Number.isFinite(Number(declared)) || Number(declared) > STATS_BODY_LIMIT)) {
    return empty(413);
  }

  // Optional binding: if absent, the zone's WAF rules are the only limiter (as for the web form).
  if (env.STATS_RATELIMIT) {
    const { success } = await env.STATS_RATELIMIT.limit({
      key: request.headers.get("CF-Connecting-IP") ?? "unknown",
    });
    if (!success) return empty(429);
  }

  const buf = await request.arrayBuffer();
  if (buf.byteLength > STATS_BODY_LIMIT) return empty(413);
  const event = parseEvent(buf);
  if (event === null) return empty(400);

  if (env.STATS_AE) recordEvent(env.STATS_AE, event, env);
  return empty(204);
}
