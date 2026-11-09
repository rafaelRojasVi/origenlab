import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { handleWebStatsRequest, type StatsReadEnv, type WebStatsResponse } from "./stats_read";
import worker from "./index";
import { AUTH_SESSION_COOKIE } from "./auth";

const DASHBOARD = "https://dashboard.origenlab.cl";
const WEB_STATS_URL = `${DASHBOARD}/api/stats/web-stats`;

const BASE_ENV: StatsReadEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
  CF_ACCOUNT_ID: "abc123accountid",
  CF_STATS_API_TOKEN: "token-for-stats",
};

/** A minimal AE SQL response with one event row. */
function aeResponse(rows: Record<string, unknown>[] = []): Response {
  return new Response(JSON.stringify({ data: rows, meta: [], rows: rows.length }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Stubs global fetch to return a session-200 response then one or two AE responses. */
function stubFetchSequence(responses: Response[]) {
  const queue = [...responses];
  const fetchMock = vi.fn(async (_req: Request) => {
    const r = queue.shift();
    if (!r) throw new Error("fetch called more times than expected");
    return r;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sessionOk(): Response {
  return new Response(null, { status: 200 });
}

function sessionUnauthorized(): Response {
  return new Response(null, { status: 401 });
}

function withSession(url = WEB_STATS_URL): Request {
  return new Request(url, {
    method: "GET",
    headers: { Cookie: `${AUTH_SESSION_COOKIE}=signed.session.cookie; other=1` },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ─── secret guard ────────────────────────────────────────────────────────────

describe("secrets missing → 503 not_configured", () => {
  it.each([
    [{ ...BASE_ENV, CF_ACCOUNT_ID: "" }],
    [{ ...BASE_ENV, CF_STATS_API_TOKEN: "" }],
    [{ ...BASE_ENV, CF_ACCOUNT_ID: undefined }],
    [{ ...BASE_ENV, CF_STATS_API_TOKEN: undefined }],
  ])("missing or empty secret", async (env) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await handleWebStatsRequest(withSession(), env as StatsReadEnv);
    expect(res.status).toBe(503);
    const body = await res.json() as { error: { code: string } };
    expect(body.error.code).toBe("not_configured");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ─── auth guard ──────────────────────────────────────────────────────────────

describe("session validation", () => {
  it("returns 401 when no session cookie is present", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request(WEB_STATS_URL, { method: "GET" });
    const res = await handleWebStatsRequest(req, BASE_ENV);
    expect(res.status).toBe(401);
    // No upstream call made when there is no cookie at all.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 401 when the API session check fails", async () => {
    stubFetchSequence([sessionUnauthorized()]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(401);
  });

  it.each([302, 404, 405])("treats an upstream %i from /auth/session as no session (401), never as authorized", async (status) => {
    const fetchMock = stubFetchSequence([new Response(null, { status })]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(401);
    // Only the session check ran; no analytics query.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("checks the session with GET and does not follow redirects", async () => {
    const fetchMock = stubFetchSequence([sessionOk(), new Response(JSON.stringify({ data: [] })), new Response(JSON.stringify({ data: [] }))]);
    await handleWebStatsRequest(withSession(), BASE_ENV);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("GET");
    expect(init.redirect).toBe("manual");
  });

  it("forwards only the session cookie to /auth/session, never the full cookie jar", async () => {
    const fetchMock = stubFetchSequence([
      sessionOk(),
      aeResponse(),
      aeResponse(),
    ]);
    await handleWebStatsRequest(
      new Request(WEB_STATS_URL, {
        method: "GET",
        headers: {
          Cookie: `CF_Authorization=cf-edge; ${AUTH_SESSION_COOKIE}=signed.value; tracking=x`,
        },
      }),
      BASE_ENV,
    );
    // fetch is called as fetch(url: string, init: RequestInit)
    const [sessionUrl, sessionInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(sessionUrl).toContain("/auth/session");
    // headers may be a Headers object or a plain record depending on node version.
    const hdrs = sessionInit.headers instanceof Headers
      ? sessionInit.headers
      : new Headers(sessionInit.headers as HeadersInit);
    const cookie = hdrs.get("Cookie") ?? "";
    expect(cookie).toContain(AUTH_SESSION_COOKIE);
    expect(cookie).not.toContain("CF_Authorization");
    expect(cookie).not.toContain("tracking");
  });

  it("sends the API auth token to /auth/session", async () => {
    const fetchMock = stubFetchSequence([sessionOk(), aeResponse(), aeResponse()]);
    await handleWebStatsRequest(withSession(), BASE_ENV);
    const [, sessionInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const hdrs = sessionInit.headers instanceof Headers
      ? sessionInit.headers
      : new Headers(sessionInit.headers as HeadersInit);
    expect(hdrs.get("X-OriginLab-API-Key")).toBe("server-only-token");
  });
});

// ─── SQL query shape ─────────────────────────────────────────────────────────

describe("AE SQL query", () => {
  it("calls the CF AE SQL API twice (7d and 30d) in parallel after session validation", async () => {
    const fetchMock = stubFetchSequence([sessionOk(), aeResponse(), aeResponse()]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    // The two AE calls must target the right account.
    // fetch is called as fetch(url: string, init: RequestInit)
    const aeCalls = fetchMock.mock.calls.slice(1) as unknown as [string, RequestInit][];
    for (const [url, init] of aeCalls) {
      expect(url).toContain("analytics_engine/sql");
      expect(url).toContain(BASE_ENV.CF_ACCOUNT_ID);
      const hdrs = init.headers instanceof Headers
        ? init.headers
        : new Headers(init.headers as HeadersInit);
      expect(hdrs.get("Authorization")).toBe(`Bearer ${BASE_ENV.CF_STATS_API_TOKEN}`);
    }
  });

  it("includes INTERVAL '7' DAY in the first query and '30' DAY in the second", async () => {
    const fetchMock = stubFetchSequence([sessionOk(), aeResponse(), aeResponse()]);
    await handleWebStatsRequest(withSession(), BASE_ENV);
    const bodies = fetchMock.mock.calls.slice(1).map((c) => {
      const [, init] = c as unknown as [string, RequestInit];
      return JSON.parse(init.body as string) as { query: string };
    });
    const queries = bodies.map((b) => b.query);
    expect(queries.some((q) => q.includes("INTERVAL '7' DAY"))).toBe(true);
    expect(queries.some((q) => q.includes("INTERVAL '30' DAY"))).toBe(true);
  });
});

// ─── response shape ──────────────────────────────────────────────────────────

describe("response shape", () => {
  it("returns 7d and 30d keys with the expected structure", async () => {
    const rows = [
      { event_name: "visita", modelo: "", origen: "google", pagina: "/", cnt: 10 },
      { event_name: "visita", modelo: "", origen: "interno", pagina: "/", cnt: 3 },
      { event_name: "cotizacion_click", modelo: "hielscher-up400st", origen: "", pagina: "", cnt: 4 },
      { event_name: "whatsapp_click", modelo: "hielscher-up200st", origen: "", pagina: "", cnt: 2 },
      { event_name: "form_submit", modelo: "", origen: "", pagina: "", cnt: 1 },
      { event_name: "pdf_download", modelo: "", origen: "", pagina: "", cnt: 2 },
      { event_name: "3d_interact", modelo: "", origen: "", pagina: "", cnt: 7 },
    ];
    stubFetchSequence([sessionOk(), aeResponse(rows), aeResponse(rows)]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(200);
    const body = await res.json() as WebStatsResponse;

    // Both windows present.
    expect(body["7d"]).toBeDefined();
    expect(body["30d"]).toBeDefined();
    expect(typeof body.fetched_at).toBe("string");

    const p = body["7d"];
    // «interno» excluded from acquisition total.
    expect(p.visitas_total).toBe(10);
    expect(p.visitas_interno).toBe(3);
    expect(p.visitas_by_origen["google"]).toBe(10);
    expect(p.visitas_by_origen["interno"]).toBeUndefined();

    expect(p.cotizacion_total).toBe(4);
    expect(p.cotizacion_top_models[0]).toEqual(["hielscher-up400st", 4]);

    expect(p.whatsapp_total).toBe(2);
    expect(p.whatsapp_top_models[0]).toEqual(["hielscher-up200st", 2]);

    expect(p.form_submits).toBe(1);
    expect(p.pdf_downloads).toBe(2);
    expect(p.d3_interacts).toBe(7);

    expect(p.top_pages[0]).toEqual(["/", 13]); // google(10) + interno(3)
  });

  it("returns zeroed metrics when AE returns no data", async () => {
    stubFetchSequence([sessionOk(), aeResponse(), aeResponse()]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(200);
    const body = await res.json() as WebStatsResponse;
    expect(body["7d"].visitas_total).toBe(0);
    expect(body["7d"].cotizacion_total).toBe(0);
    expect(body["7d"].top_pages).toEqual([]);
  });

  it("returns 502 when the AE SQL API fails", async () => {
    stubFetchSequence([sessionOk(), new Response("Internal Error", { status: 500 }), new Response("Internal Error", { status: 500 })]);
    const res = await handleWebStatsRequest(withSession(), BASE_ENV);
    expect(res.status).toBe(502);
    const body = await res.json() as { error: { code: string } };
    expect(body.error.code).toBe("ae_query_failed");
  });
});

// ─── HEAD method ─────────────────────────────────────────────────────────────

describe("HEAD method", () => {
  it("returns 200 with no body after session validation, without calling AE", async () => {
    const fetchMock = stubFetchSequence([sessionOk()]);
    const res = await handleWebStatsRequest(
      new Request(WEB_STATS_URL, {
        method: "HEAD",
        headers: { Cookie: `${AUTH_SESSION_COOKIE}=signed.value` },
      }),
      BASE_ENV,
    );
    expect(res.status).toBe(200);
    expect(res.body).toBeNull();
    // Only the session check; no AE call.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [sessionUrl] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(sessionUrl).toContain("/auth/session");
  });

  it("returns 405 for other methods", async () => {
    const res = await handleWebStatsRequest(
      new Request(WEB_STATS_URL, { method: "POST" }),
      BASE_ENV,
    );
    expect(res.status).toBe(405);
  });
});

// ─── Worker routing ──────────────────────────────────────────────────────────

describe("Worker routing", () => {
  it("routes GET /api/stats/web-stats on dashboard hostname to the stats-read handler", async () => {
    // The Worker will call validateSession → AE × 2.
    const fetchMock = stubFetchSequence([sessionOk(), aeResponse(), aeResponse()]);
    const res = await worker.fetch(
      new Request(WEB_STATS_URL, {
        method: "GET",
        headers: { Cookie: `${AUTH_SESSION_COOKIE}=signed.value` },
      }),
      BASE_ENV as any,
    );
    // Session validated + two AE calls = 200.
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("does NOT route public site /api/stats/web-stats to the stats-read handler", async () => {
    // A request to origenlab.cl/api/stats/web-stats is public stats territory → 404.
    const res = await worker.fetch(
      new Request("https://origenlab.cl/api/stats/web-stats", {
        method: "GET",
        headers: { Origin: "https://origenlab.cl" },
      }),
      BASE_ENV as any,
    );
    // handleStatsRequest covers /api/stats/* on the public site; web-stats is not a known event path → 404.
    expect(res.status).toBe(404);
  });
});
