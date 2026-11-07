import type { ProxyEnv } from "./proxy";

/**
 * First-party analytics under `origenlab.cl/api/stats/*`.
 *
 * The public site's CSP is `connect-src 'self'`, so every analytics call stays on origenlab.cl.
 * This Worker route family is public (like `/api/web/*`), has no session, no operator identity
 * and no CORS, and is separate from the dashboard allowlist. Exactly two requests exist:
 *
 *   POST /api/stats/event      one of six named events, `{name, props}`: the five click events take
 *                              `props:{modelo?}`, `visita` takes `props:{origen, pagina}`
 *   GET  /api/stats/script.js  the analytics script, only when an analytics origin is configured
 *
 * Two modes, picked by `STATS_ORIGIN`:
 *   - set: a sanitized event is forwarded to the self-hosted analytics origin (Umami or Plausible
 *     CE; the Worker does not care which, see STATS_EVENT_PATH / STATS_SCRIPT_PATH);
 *   - empty: the Worker counts the event itself in KV (daily counters) and answers 204.
 *
 * Nothing here logs an IP, a User-Agent or a body. The analytics origin never receives a cookie,
 * an `Authorization` header, the API key or Access credentials: upstream headers are built from
 * scratch, not copied.
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
]);

const STATS_SITE_ORIGIN = "https://origenlab.cl";
const EVENT_PATH = "/api/stats/event";
const SCRIPT_PATH = "/api/stats/script.js";
const DEFAULT_UPSTREAM_EVENT_PATH = "/api/event"; // Plausible CE; Umami is "/api/send"
const DEFAULT_UPSTREAM_SCRIPT_PATH = "/js/script.js"; // Plausible CE; Umami is "/script.js"
const COUNTER_TTL_SECONDS = 60 * 60 * 24 * 90;
const PAGINA_RE = /^(?!.*\/\/)\/[a-z0-9/-]{0,80}$/;
const MODELO_RE = /^[A-Za-z0-9._-]{1,64}$/;
const UPSTREAM_PATH_RE = /^\/[A-Za-z0-9._\-/]*$/;

interface RateLimit {
  limit(opts: { key: string }): Promise<{ success: boolean }>;
}

export type StatsEnv = ProxyEnv & {
  STATS_ORIGIN?: string;
  STATS_EVENT_PATH?: string;
  STATS_SCRIPT_PATH?: string;
  /** Comma-separated modelo slugs the counter may key on; anything else counts as "other". */
  STATS_MODELOS?: string;
  /** Comma-separated page paths a visit counter may key on; any other page counts as "otra". */
  STATS_PAGINAS?: string;
  STATS_RATELIMIT?: RateLimit;
  STATS_KV?: KVNamespace;
};

interface StatsEvent {
  name: string;
  modelo: string | undefined;
  origen: string | undefined;
  pagina: string | undefined;
}

const empty = (status: number, headers?: Record<string, string>): Response =>
  new Response(null, { status, headers: { "Cache-Control": "no-store", ...headers } });

/** `https://host[:port]` only: no credentials, path, query or fragment. Null when misconfigured. */
function parseStatsOrigin(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (
    url.protocol !== "https:" ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    return null;
  }
  return url.origin;
}

/** An absolute path with no query, no `..` and no scheme-relative `//`. Null when misconfigured. */
function upstreamPath(configured: string | undefined, fallback: string): string | null {
  const raw = configured?.trim() || fallback;
  if (!UPSTREAM_PATH_RE.test(raw) || raw.startsWith("//") || raw.split("/").includes("..")) {
    return null;
  }
  return raw;
}

/** Headers the analytics origin gets: a fixed few, never a copy of the browser's. */
function upstreamHeaders(request: Request, withBody: boolean): Headers {
  const headers = new Headers({ Accept: "*/*" });
  if (withBody) headers.set("Content-Type", "application/json");
  const userAgent = request.headers.get("User-Agent");
  if (userAgent) headers.set("User-Agent", userAgent);
  // Visitor counting on the analytics origin needs the client address; it is forwarded, never logged.
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) headers.set("X-Forwarded-For", ip);
  return headers;
}

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
 * Best-effort daily counter, `daily:{YYYY-MM-DD}:{name}:{modelo}`. The modelo is part of the key only
 * when it is in `STATS_MODELOS`; any other value counts as "other", so a client cannot mint keys
 * (an event with no modelo keeps the empty segment). Read-then-write, so two events
 * landing in the same instant can lose one count: acceptable for coarse click totals, and
 * counting never blocks or fails the visitor's request.
 */
async function bump(kv: KVNamespace, key: string): Promise<void> {
  try {
    const current = parseInt((await kv.get(key)) ?? "0", 10);
    await kv.put(key, String(Number.isFinite(current) ? current + 1 : 1), {
      expirationTtl: COUNTER_TTL_SECONDS,
    });
  } catch {
    console.warn("stats_counter_failed"); // a fixed string: no key, no IP, no body
  }
}

async function incrementCounter(kv: KVNamespace, event: StatsEvent, env: StatsEnv): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  if (event.name === "visita" && event.origen !== undefined && event.pagina !== undefined) {
    // ONE write per visit (KV's free plan allows about 1,000 writes a day). Both segments are bounded:
    // origen is a closed set of six, pagina is STATS_PAGINAS or "otra", so at most
    // 6 x (STATS_PAGINAS size + 1) keys per day. A per-source total is the sum over its pages at read time.
    const pagina = allowedPaginas(env.STATS_PAGINAS).has(event.pagina) ? event.pagina : OTHER_PAGINA;
    await bump(kv, `daily:${today}:visita:${event.origen}:${pagina}`);
    return;
  }
  const known = allowedModelos(env.STATS_MODELOS);
  const segment = event.modelo === undefined ? "" : known.has(event.modelo) ? event.modelo : OTHER_MODELO;
  await bump(kv, `daily:${today}:${event.name}:${segment}`);
}

export async function handleStatsRequest(request: Request, env: StatsEnv): Promise<Response> {
  const url = new URL(request.url);
  const isEvent = url.pathname === EVENT_PATH && request.method === "POST";
  const isScript = url.pathname === SCRIPT_PATH && request.method === "GET";
  // Only two method+path pairs exist; everything else is indistinguishable from a missing route.
  if (!isEvent && !isScript) return empty(404);

  const configuredOrigin = env.STATS_ORIGIN?.trim() ?? "";
  const proxyMode = configuredOrigin !== "";
  // script.js has nothing to serve without an analytics origin.
  if (isScript && !proxyMode) return empty(404);

  if (isEvent) {
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
  }

  // A mistyped STATS_ORIGIN or path is a deployment error, never a silent switch to counting.
  let upstreamBase: string | null = null;
  let path: string | null = null;
  if (proxyMode) {
    upstreamBase = parseStatsOrigin(configuredOrigin);
    path = isEvent
      ? upstreamPath(env.STATS_EVENT_PATH, DEFAULT_UPSTREAM_EVENT_PATH)
      : upstreamPath(env.STATS_SCRIPT_PATH, DEFAULT_UPSTREAM_SCRIPT_PATH);
    if (upstreamBase === null || path === null) return empty(500);
  }

  // Optional binding: if absent, the zone's WAF rules are the only limiter (as for the web form).
  if (env.STATS_RATELIMIT) {
    const { success } = await env.STATS_RATELIMIT.limit({
      key: request.headers.get("CF-Connecting-IP") ?? "unknown",
    });
    if (!success) return empty(429);
  }

  if (isScript) {
    let upstream: Response;
    try {
      upstream = await fetch(
        new Request(`${upstreamBase}${path}`, {
          method: "GET",
          headers: upstreamHeaders(request, false),
          redirect: "manual",
        }),
      );
    } catch {
      return empty(502);
    }
    if (upstream.status >= 300 && upstream.status < 400) return empty(502);
    // Rebuilt, not copied: no Set-Cookie, no upstream-specific headers.
    const headers = new Headers({
      "Cache-Control": upstream.ok ? "public, max-age=3600" : "no-store",
    });
    // Forced, not echoed: whatever the upstream says, the browser gets a script and may not sniff it.
    headers.set("Content-Type", "application/javascript; charset=utf-8");
    headers.set("X-Content-Type-Options", "nosniff");
    return new Response(upstream.body, { status: upstream.status, headers });
  }

  const buf = await request.arrayBuffer();
  if (buf.byteLength > STATS_BODY_LIMIT) return empty(413);
  const event = parseEvent(buf);
  if (event === null) return empty(400);

  if (!proxyMode) {
    if (env.STATS_KV) await incrementCounter(env.STATS_KV, event, env);
    return empty(204);
  }

  // Only the validated shape is forwarded; whatever else the browser sent stops here.
  const body = JSON.stringify({
    name: event.name,
    props:
      event.name === "visita"
        ? { origen: event.origen, pagina: event.pagina }
        : event.modelo === undefined
          ? {}
          : { modelo: event.modelo },
  });
  let upstream: Response;
  try {
    upstream = await fetch(
      new Request(`${upstreamBase}${path}`, {
        method: "POST",
        headers: upstreamHeaders(request, true),
        body,
        redirect: "manual",
      }),
    );
  } catch {
    return empty(502);
  }
  // The body is never read; release the connection. A failed cancel must not
  // become a 500 for the visitor.
  try {
    await upstream.body?.cancel();
  } catch {
    // ignore
  }
  if (upstream.status >= 300 && upstream.status < 400) return empty(502);
  return empty(upstream.status);
}
