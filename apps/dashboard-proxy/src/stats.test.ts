import { afterEach, describe, expect, it, vi } from "vitest";

import { STATS_BODY_LIMIT, STATS_EVENT_NAMES, STATS_ORIGENES, handleStatsRequest } from "../src/stats";
import worker from "../src/index";
import type { ProxyEnv } from "../src/proxy";

const SITE_ORIGIN = "https://origenlab.cl";
const EVENT_URL = "https://origenlab.cl/api/stats/event";
const SCRIPT_URL = "https://origenlab.cl/api/stats/script.js";

const BASE_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
  CF_ACCESS_CLIENT_ID: "cf-client-id",
  CF_ACCESS_CLIENT_SECRET: "cf-client-secret",
};

/** An in-memory KVNamespace double: only what the counter touches. */
function makeKv() {
  const store = new Map<string, string>();
  const put = vi.fn(async (key: string, value: string, _opts?: unknown) => {
    store.set(key, value);
  });
  const kv = {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    put,
  } as unknown as KVNamespace;
  return { kv, store, put };
}

function proxyEnv(extra: Record<string, unknown> = {}) {
  return { ...BASE_ENV, STATS_ORIGIN: "https://stats.example.invalid", ...extra };
}

function eventRequest(
  body: unknown = { name: "cotizacion_click", props: { modelo: "UP400St" } },
  opts: {
    origin?: string | null;
    method?: string;
    contentType?: string | null;
    headers?: Record<string, string>;
    url?: string;
    raw?: string;
  } = {},
): Request {
  const headers = new Headers(opts.headers ?? {});
  if (opts.origin !== null) headers.set("Origin", opts.origin ?? SITE_ORIGIN);
  if (opts.contentType !== null) headers.set("Content-Type", opts.contentType ?? "application/json");
  const method = opts.method ?? "POST";
  return new Request(opts.url ?? EVENT_URL, {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : (opts.raw ?? JSON.stringify(body)),
  });
}

function stubFetch(status = 202, body: BodyInit | null = null, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async (_req: Request) => new Response(body, { status, headers }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("stats paths and methods (fail closed)", () => {
  it.each([
    ["GET", "https://origenlab.cl/api/stats/admin"],
    ["GET", "https://origenlab.cl/api/stats/"],
    ["GET", "https://origenlab.cl/api/stats/event"],
    ["POST", "https://origenlab.cl/api/stats/event/"],
    ["POST", "https://origenlab.cl/api/stats/script.js"],
    ["POST", "https://origenlab.cl/api/stats/admin"],
    ["POST", "https://origenlab.cl/api/stats/event/../admin"],
    ["POST", "https://origenlab.cl/api/stats/api/event"],
    ["GET", "https://origenlab.cl/api/stats/script.js/"],
    ["GET", "https://origenlab.cl/api/stats/Script.js"],
    ["DELETE", EVENT_URL],
    ["PUT", EVENT_URL],
    ["PATCH", EVENT_URL],
    ["OPTIONS", EVENT_URL],
    ["HEAD", SCRIPT_URL],
  ])("%s %s is 404 and never reaches upstream", async (method, url) => {
    const fetchMock = stubFetch();
    const { kv, put } = makeKv();
    const resp = await handleStatsRequest(eventRequest(undefined, { method, url }), proxyEnv({ STATS_KV: kv }));
    expect(resp.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it("returns 404 with no body and no cookies", async () => {
    const resp = await handleStatsRequest(eventRequest(undefined, { method: "DELETE" }), proxyEnv());
    expect(await resp.text()).toBe("");
    expect(resp.headers.get("Set-Cookie")).toBeNull();
  });
});

describe("stats event guards", () => {
  it("refuses a missing Origin with 403", async () => {
    const fetchMock = stubFetch();
    const resp = await handleStatsRequest(eventRequest(undefined, { origin: null }), proxyEnv());
    expect(resp.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["https://dashboard.origenlab.cl", "https://evil.example.invalid", "http://origenlab.cl", "null"])(
    "refuses Origin %s with 403",
    async (origin) => {
      const fetchMock = stubFetch();
      const resp = await handleStatsRequest(eventRequest(undefined, { origin }), proxyEnv());
      expect(resp.status).toBe(403);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("refuses Sec-Fetch-Site: cross-site with 403", async () => {
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "Sec-Fetch-Site": "cross-site" } }),
      proxyEnv(),
    );
    expect(resp.status).toBe(403);
  });

  it("refuses a form content type with 415", async () => {
    const resp = await handleStatsRequest(
      eventRequest(undefined, { contentType: "application/x-www-form-urlencoded", raw: "name=whatsapp_click" }),
      proxyEnv(),
    );
    expect(resp.status).toBe(415);
  });

  it("accepts text/plain, which is what sendBeacon sends for a string", async () => {
    const fetchMock = stubFetch(202);
    const resp = await handleStatsRequest(eventRequest(undefined, { contentType: "text/plain;charset=UTF-8" }), proxyEnv());
    expect(resp.status).toBe(202);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("refuses a declared Content-Length over the cap with 413", async () => {
    const fetchMock = stubFetch();
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "Content-Length": String(STATS_BODY_LIMIT + 1) } }),
      proxyEnv(),
    );
    expect(resp.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an actual body over the cap with 413", async () => {
    const fetchMock = stubFetch();
    const big = { name: "cotizacion_click", props: { modelo: "x" }, pad: "p".repeat(STATS_BODY_LIMIT) };
    const resp = await handleStatsRequest(eventRequest(big), proxyEnv());
    expect(resp.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses invalid JSON with 400", async () => {
    const resp = await handleStatsRequest(eventRequest(undefined, { raw: "{not json" }), proxyEnv());
    expect(resp.status).toBe(400);
  });

  it.each([null, [], "cotizacion_click", 7])("refuses a non-object body %j with 400", async (body) => {
    const resp = await handleStatsRequest(eventRequest(body), proxyEnv());
    expect(resp.status).toBe(400);
  });

  it.each([
    [{ name: "pageview" }],
    [{ name: "COTIZACION_CLICK" }],
    [{ name: "" }],
    [{ name: 3 }],
    [{ props: { modelo: "UP400St" } }],
    [{ name: "__proto__" }],
    [{ name: "constructor" }],
  ])("refuses event %j (not one of the five names) with 400", async (body) => {
    const fetchMock = stubFetch();
    const { kv, put } = makeKv();
    const resp = await handleStatsRequest(eventRequest(body), proxyEnv({ STATS_KV: kv }));
    expect(resp.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it.each([
    [{ name: "cotizacion_click", props: "UP400St" }],
    [{ name: "cotizacion_click", props: [] }],
    [{ name: "cotizacion_click", props: { modelo: 5 } }],
    [{ name: "cotizacion_click", props: { modelo: "x".repeat(65) } }],
    [{ name: "cotizacion_click", props: { modelo: "a b@c.invalid" } }],
    [{ name: "cotizacion_click", props: { modelo: "UP400St:daily" } }],
    [{ name: "cotizacion_click", props: { modelo: "" } }],
  ])("refuses bad props %j with 400", async (body) => {
    const resp = await handleStatsRequest(eventRequest(body), proxyEnv());
    expect(resp.status).toBe(400);
  });

  it("knows exactly the six event names", () => {
    expect([...STATS_EVENT_NAMES].sort()).toEqual(
      ["3d_interact", "cotizacion_click", "form_submit", "pdf_download", "visita", "whatsapp_click"].sort(),
    );
  });

  it("knows exactly the six visit origins", () => {
    expect([...STATS_ORIGENES].sort()).toEqual(
      ["directo", "google", "linkedin", "otro", "perfil-google", "whatsapp"].sort(),
    );
  });

  it.each(["cotizacion_click", "whatsapp_click", "pdf_download", "3d_interact", "form_submit"])(
    "%s still refuses origen and pagina with 400",
    async (name) => {
      const fetchMock = stubFetch();
      const { kv, put } = makeKv();
      for (const props of [{ origen: "google" }, { pagina: "/catalogo" }, { modelo: "UP400St", origen: "google", pagina: "/" }]) {
        const resp = await handleStatsRequest(eventRequest({ name, props }), proxyEnv({ STATS_KV: kv }));
        expect(resp.status).toBe(400);
      }
      expect(fetchMock).not.toHaveBeenCalled();
      expect(put).not.toHaveBeenCalled();
    },
  );
});

describe("stats visita event", () => {
  const ORIGENES = ["google", "perfil-google", "linkedin", "directo", "whatsapp", "otro"];

  it.each(ORIGENES)("accepts origen %s", async (origen) => {
    stubFetch(202);
    const resp = await handleStatsRequest(eventRequest({ name: "visita", props: { origen, pagina: "/catalogo/ultrasonido" } }), proxyEnv());
    expect(resp.status).toBe(202);
  });

  it.each([
    ["bad origen", { origen: "facebook", pagina: "/" }],
    ["uppercase origen", { origen: "Google", pagina: "/" }],
    ["non-string origen", { origen: 3, pagina: "/" }],
    ["origen with a separator", { origen: "google:x", pagina: "/" }],
    ["missing origen", { pagina: "/" }],
    ["missing pagina", { origen: "google" }],
    ["no props", undefined],
    ["pagina too long", { origen: "google", pagina: "/" + "a".repeat(81) }],
    ["pagina with query", { origen: "google", pagina: "/catalogo?utm=x" }],
    ["pagina with fragment", { origen: "google", pagina: "/catalogo#x" }],
    ["pagina uppercase", { origen: "google", pagina: "/Catalogo" }],
    ["pagina without leading slash", { origen: "google", pagina: "catalogo" }],
    ["pagina with colon", { origen: "google", pagina: "/a:b" }],
    ["pagina with a double slash", { origen: "google", pagina: "/a//b" }],
    ["pagina starting with a double slash", { origen: "google", pagina: "//evil" }],
    ["pagina with an encoded slash", { origen: "google", pagina: "/a%2fb" }],
    ["pagina with dot segments", { origen: "google", pagina: "/a/../b" }],
    ["pagina with dot", { origen: "google", pagina: "/a.html" }],
    ["pagina empty", { origen: "google", pagina: "" }],
    ["pagina a full URL", { origen: "google", pagina: "https://origenlab.cl/" }],
    ["non-string pagina", { origen: "google", pagina: 7 }],
    ["a modelo", { origen: "google", pagina: "/", modelo: "UP400St" }],
  ])("refuses %s with 400 and stores nothing", async (_label, props) => {
    const fetchMock = stubFetch();
    const { kv, put } = makeKv();
    const resp = await handleStatsRequest(eventRequest({ name: "visita", props }), proxyEnv({ STATS_KV: kv }));
    expect(resp.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it("accepts the root path and a path of exactly 81 characters", async () => {
    stubFetch(202);
    for (const pagina of ["/", "/" + "a".repeat(80)]) {
      const resp = await handleStatsRequest(eventRequest({ name: "visita", props: { origen: "directo", pagina } }), proxyEnv());
      expect(resp.status).toBe(202);
    }
  });

  it("forwards only {origen, pagina} in proxy mode", async () => {
    const fetchMock = stubFetch(202);
    await handleStatsRequest(
      eventRequest({ name: "visita", props: { origen: "linkedin", pagina: "/contacto", email: "a@b.invalid" } }),
      proxyEnv(),
    );
    expect(await fetchMock.mock.calls[0][0].json()).toEqual({
      name: "visita",
      props: { origen: "linkedin", pagina: "/contacto" },
    });
  });
});

describe("stats rate limit", () => {
  it("returns 429 when the limiter says no, before upstream or counter", async () => {
    const fetchMock = stubFetch();
    const { kv, put } = makeKv();
    const limit = vi.fn(async (_o: { key: string }) => ({ success: false }));
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "CF-Connecting-IP": "203.0.113.9" } }),
      proxyEnv({ STATS_RATELIMIT: { limit }, STATS_KV: kv }),
    );
    expect(resp.status).toBe(429);
    expect(limit).toHaveBeenCalledWith({ key: "203.0.113.9" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it("lets the request through when the limiter allows it", async () => {
    stubFetch(202);
    const limit = vi.fn(async (_o: { key: string }) => ({ success: true }));
    const resp = await handleStatsRequest(eventRequest(), proxyEnv({ STATS_RATELIMIT: { limit } }));
    expect(resp.status).toBe(202);
  });

  it("does not rate-limit refused requests (wrong origin never spends a token)", async () => {
    const limit = vi.fn(async (_o: { key: string }) => ({ success: true }));
    await handleStatsRequest(eventRequest(undefined, { origin: "https://evil.example.invalid" }), proxyEnv({ STATS_RATELIMIT: { limit } }));
    expect(limit).not.toHaveBeenCalled();
  });

  it("rate-limits script.js too", async () => {
    const fetchMock = stubFetch();
    const limit = vi.fn(async (_o: { key: string }) => ({ success: false }));
    const resp = await handleStatsRequest(
      new Request(SCRIPT_URL),
      proxyEnv({ STATS_RATELIMIT: { limit } }),
    );
    expect(resp.status).toBe(429);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("stats proxy mode (STATS_ORIGIN set)", () => {
  it("forwards a sanitized event without cookies, credentials or the API key, and returns the upstream status", async () => {
    const fetchMock = stubFetch(202, "accepted", { "Set-Cookie": "sid=abc", "Content-Type": "text/plain" });
    const req = eventRequest(
      { name: "whatsapp_click", props: { modelo: "UP400St", email: "a@b.invalid" }, extra: 1 },
      {
        headers: {
          Cookie: "session=secret",
          Authorization: "Bearer secret",
          "X-OriginLab-API-Key": "k",
          "User-Agent": "UA/1.0",
          "CF-Connecting-IP": "203.0.113.9",
        },
      },
    );
    const resp = await handleStatsRequest(req, proxyEnv());

    expect(resp.status).toBe(202);
    expect(resp.headers.get("Set-Cookie")).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.url).toBe("https://stats.example.invalid/api/event");
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Cookie")).toBeNull();
    expect(upstream.headers.get("Authorization")).toBeNull();
    expect(upstream.headers.get("X-OriginLab-API-Key")).toBeNull();
    expect(upstream.headers.get("CF-Access-Client-Id")).toBeNull();
    expect(upstream.headers.get("Origin")).toBeNull();
    expect(upstream.headers.get("Content-Type")).toBe("application/json");
    expect(upstream.headers.get("User-Agent")).toBe("UA/1.0");
    expect(upstream.headers.get("X-Forwarded-For")).toBe("203.0.113.9");
    expect(upstream.redirect).toBe("manual");
    // Only the allowlisted shape reaches the analytics origin.
    expect(await upstream.json()).toEqual({ name: "whatsapp_click", props: { modelo: "UP400St" } });
  });

  it("passes an upstream error status through", async () => {
    stubFetch(500);
    const resp = await handleStatsRequest(eventRequest(), proxyEnv());
    expect(resp.status).toBe(500);
  });

  it("turns an upstream redirect into 502 and does not follow it", async () => {
    stubFetch(302, null, { Location: "https://elsewhere.example.invalid/" });
    const resp = await handleStatsRequest(eventRequest(), proxyEnv());
    expect(resp.status).toBe(502);
    expect(resp.headers.get("Location")).toBeNull();
  });

  it("turns an unreachable upstream into 502", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("boom"); }));
    const resp = await handleStatsRequest(eventRequest(), proxyEnv());
    expect(resp.status).toBe(502);
  });

  it("does not touch the KV counter in proxy mode", async () => {
    stubFetch(202);
    const { kv, put } = makeKv();
    await handleStatsRequest(eventRequest(), proxyEnv({ STATS_KV: kv }));
    expect(put).not.toHaveBeenCalled();
  });

  it("honours STATS_EVENT_PATH and tolerates a trailing slash on STATS_ORIGIN", async () => {
    const fetchMock = stubFetch(200);
    await handleStatsRequest(
      eventRequest(),
      proxyEnv({ STATS_ORIGIN: "https://stats.example.invalid/", STATS_EVENT_PATH: "/api/send" }),
    );
    expect(fetchMock.mock.calls[0][0].url).toBe("https://stats.example.invalid/api/send");
  });

  it.each(["http://stats.example.invalid", "stats.example.invalid", "https://stats.example.invalid/prefix", "https://user:pw@stats.example.invalid"])(
    "treats STATS_ORIGIN %s as a misconfiguration (500), not as fallback mode",
    async (origin) => {
      const fetchMock = stubFetch();
      const { kv, put } = makeKv();
      const resp = await handleStatsRequest(eventRequest(), proxyEnv({ STATS_ORIGIN: origin, STATS_KV: kv }));
      expect(resp.status).toBe(500);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(put).not.toHaveBeenCalled();
    },
  );

  it.each(["api/event", "//evil.example.invalid/x", "/a/../b", "/a?x=1"])(
    "treats STATS_EVENT_PATH %s as a misconfiguration (500)",
    async (path) => {
      const fetchMock = stubFetch();
      const resp = await handleStatsRequest(eventRequest(), proxyEnv({ STATS_EVENT_PATH: path }));
      expect(resp.status).toBe(500);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});

describe("stats script.js", () => {
  it("is proxied in proxy mode with no cookies, ignoring any query string", async () => {
    const fetchMock = stubFetch(200, "console.log(1)", {
      "Content-Type": "text/html",
      "Set-Cookie": "sid=abc",
      "X-Secret": "nope",
    });
    const resp = await handleStatsRequest(
      new Request(`${SCRIPT_URL}?x=1`, { headers: { Cookie: "session=secret", Authorization: "Bearer s", "X-OriginLab-API-Key": "k" } }),
      proxyEnv(),
    );
    expect(resp.status).toBe(200);
    expect(await resp.text()).toBe("console.log(1)");
    expect(resp.headers.get("Content-Type")).toBe("application/javascript; charset=utf-8");
    expect(resp.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(resp.headers.get("Cache-Control")).toBe("public, max-age=3600");
    expect(resp.headers.get("Set-Cookie")).toBeNull();
    expect(resp.headers.get("X-Secret")).toBeNull();
    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.method).toBe("GET");
    expect(upstream.url).toBe("https://stats.example.invalid/js/script.js");
    expect(upstream.headers.get("Cookie")).toBeNull();
    expect(upstream.headers.get("Authorization")).toBeNull();
    expect(upstream.headers.get("X-OriginLab-API-Key")).toBeNull();
  });

  it("honours STATS_SCRIPT_PATH", async () => {
    const fetchMock = stubFetch(200, "x");
    await handleStatsRequest(new Request(SCRIPT_URL), proxyEnv({ STATS_SCRIPT_PATH: "/script.js" }));
    expect(fetchMock.mock.calls[0][0].url).toBe("https://stats.example.invalid/script.js");
  });

  it.each([undefined, "", "   "])("is 404 in fallback mode (STATS_ORIGIN=%j)", async (origin) => {
    const fetchMock = stubFetch();
    const resp = await handleStatsRequest(new Request(SCRIPT_URL), { ...BASE_ENV, STATS_ORIGIN: origin });
    expect(resp.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("turns an upstream redirect into 502", async () => {
    stubFetch(301, null, { Location: "https://elsewhere.example.invalid/s.js" });
    const resp = await handleStatsRequest(new Request(SCRIPT_URL), proxyEnv());
    expect(resp.status).toBe(502);
  });

  it("does not cache an upstream failure", async () => {
    stubFetch(500, "oops");
    const resp = await handleStatsRequest(new Request(SCRIPT_URL), proxyEnv());
    expect(resp.status).toBe(500);
    expect(resp.headers.get("Cache-Control")).toBe("no-store");
  });
});

describe("stats fallback counter (STATS_ORIGIN empty)", () => {
  const today = () => new Date().toISOString().slice(0, 10);

  it("increments the daily counter and answers 204 with no body", async () => {
    const fetchMock = stubFetch();
    const { kv, store, put } = makeKv();
    const env = { ...BASE_ENV, STATS_ORIGIN: "", STATS_KV: kv, STATS_MODELOS: "UP400St, UP200St" };

    const first = await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);
    expect(first.status).toBe(204);
    expect(await first.text()).toBe("");
    await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);

    expect(store.get(`daily:${today()}:pdf_download:UP400St`)).toBe("2");
    expect(put).toHaveBeenLastCalledWith(
      `daily:${today()}:pdf_download:UP400St`,
      "2",
      { expirationTtl: 60 * 60 * 24 * 90 },
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keys by name and modelo, with an empty modelo when none is given", async () => {
    const { kv, store } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_MODELOS: "UP400St,UP200St" };
    await handleStatsRequest(eventRequest({ name: "form_submit" }), env);
    await handleStatsRequest(eventRequest({ name: "form_submit", props: {} }), env);
    await handleStatsRequest(eventRequest({ name: "3d_interact", props: { modelo: "UP200St" } }), env);
    expect([...store.entries()].sort()).toEqual(
      [
        [`daily:${today()}:3d_interact:UP200St`, "1"],
        [`daily:${today()}:form_submit:`, "2"],
      ].sort(),
    );
  });

  it("keeps an allowlisted modelo and collapses everything else to one \"other\" key", async () => {
    const { kv, store } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_MODELOS: "UP400St,UP200St" };
    for (const modelo of ["UP400St", "rnd-9f3a1", "rnd-77c0e", "rnd-0b2d4"]) {
      await handleStatsRequest(eventRequest({ name: "whatsapp_click", props: { modelo } }), env);
    }
    expect([...store.entries()].sort()).toEqual(
      [
        [`daily:${today()}:whatsapp_click:UP400St`, "1"],
        [`daily:${today()}:whatsapp_click:other`, "3"],
      ].sort(),
    );
  });

  it("collapses every modelo to \"other\" when STATS_MODELOS is empty or unset", async () => {
    for (const modelos of [undefined, "", " , "]) {
      const { kv, store } = makeKv();
      const env = { ...BASE_ENV, STATS_KV: kv, STATS_MODELOS: modelos };
      await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);
      await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "anything-else" } }), env);
      expect([...store.keys()]).toEqual([`daily:${today()}:pdf_download:other`]);
    }
  });

  it("starts a new key when the UTC date rolls over", async () => {
    vi.useFakeTimers();
    try {
      const { kv, store } = makeKv();
      const env = { ...BASE_ENV, STATS_KV: kv };
      vi.setSystemTime(new Date("2026-11-07T23:59:59Z"));
      await handleStatsRequest(eventRequest({ name: "whatsapp_click" }), env);
      vi.setSystemTime(new Date("2026-11-08T00:00:01Z"));
      await handleStatsRequest(eventRequest({ name: "whatsapp_click" }), env);
      expect(store.get("daily:2026-11-07:whatsapp_click:")).toBe("1");
      expect(store.get("daily:2026-11-08:whatsapp_click:")).toBe("1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("still answers 204 when the KV write fails, and logs no personal data", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const kv = {
      get: vi.fn(async () => { throw new Error("kv down"); }),
      put: vi.fn(),
    } as unknown as KVNamespace;
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "CF-Connecting-IP": "203.0.113.9", "User-Agent": "UA/1.0" } }),
      { ...BASE_ENV, STATS_KV: kv },
    );
    expect(resp.status).toBe(204);
    const logged = JSON.stringify([...warn.mock.calls, ...error.mock.calls]);
    expect(logged).not.toContain("203.0.113.9");
    expect(logged).not.toContain("UA/1.0");
  });

  it("answers 204 without storing anything when no KV is bound", async () => {
    const resp = await handleStatsRequest(eventRequest(), { ...BASE_ENV });
    expect(resp.status).toBe(204);
  });

  it("does not store an event that failed validation", async () => {
    const { kv, put } = makeKv();
    const resp = await handleStatsRequest(eventRequest({ name: "pageview" }), { ...BASE_ENV, STATS_KV: kv });
    expect(resp.status).toBe(400);
    expect(put).not.toHaveBeenCalled();
  });
});

describe("stats visita counter", () => {
  const today = () => new Date().toISOString().slice(0, 10);
  const visita = (origen: string, pagina: string) => eventRequest({ name: "visita", props: { origen, pagina } });

  it("counts an allowlisted page under its own key with a single KV write per visit", async () => {
    const { kv, store, put } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: "/, /catalogo ,/contacto" };
    await handleStatsRequest(visita("google", "/catalogo"), env);
    await handleStatsRequest(visita("google", "/catalogo"), env);
    await handleStatsRequest(visita("google", "/"), env);
    expect(put).toHaveBeenCalledTimes(3);
    expect([...store.entries()].sort()).toEqual(
      [
        [`daily:${today()}:visita:google:/catalogo`, "2"],
        [`daily:${today()}:visita:google:/`, "1"],
      ].sort(),
    );
  });

  it("collapses every page outside STATS_PAGINAS to one \"otra\" key", async () => {
    const { kv, store } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: "/catalogo" };
    for (const pagina of ["/rnd-9f3a1", "/rnd-77c0e", "/catalogo/ultrasonido"]) {
      await handleStatsRequest(visita("linkedin", pagina), env);
    }
    expect([...store.entries()].sort()).toEqual(
      [
        [`daily:${today()}:visita:linkedin:otra`, "3"],
      ].sort(),
    );
  });

  it("matches the page list exactly: no prefix, no trailing-slash folding", async () => {
    const { kv, store } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: "/catalogo" };
    await handleStatsRequest(visita("directo", "/catalogo/"), env);
    expect(store.has(`daily:${today()}:visita:directo:otra`)).toBe(true);
    expect(store.has(`daily:${today()}:visita:directo:/catalogo/`)).toBe(false);
  });

  it("collapses every page to \"otra\" when STATS_PAGINAS is empty or unset", async () => {
    for (const paginas of [undefined, "", " , "]) {
      const { kv, store } = makeKv();
      const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: paginas };
      await handleStatsRequest(visita("whatsapp", "/"), env);
      await handleStatsRequest(visita("whatsapp", "/catalogo"), env);
      expect([...store.entries()].sort()).toEqual(
        [
          [`daily:${today()}:visita:whatsapp:otra`, "2"],
        ].sort(),
      );
    }
  });

  it("ignores malformed STATS_PAGINAS entries instead of keying on them", async () => {
    const { kv, store } = makeKv();
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: "/Catalogo,catalogo,/a?b,/ok" };
    await handleStatsRequest(visita("google", "/ok"), env);
    expect([...store.keys()]).toEqual([`daily:${today()}:visita:google:/ok`]);
  });

  it("keeps the keys per day within 6 origins x (allowlist + 1), whatever the client sends", async () => {
    const { kv, store } = makeKv();
    const allowlist = ["/", "/catalogo", "/contacto", "/nosotros"];
    const env = { ...BASE_ENV, STATS_KV: kv, STATS_PAGINAS: allowlist.join(",") };
    const pages = [...allowlist, ...Array.from({ length: 25 }, (_, i) => `/rnd-${i}`)];
    for (const origen of STATS_ORIGENES) {
      for (const pagina of pages) await handleStatsRequest(visita(origen, pagina), env);
    }
    const maximum = STATS_ORIGENES.size * (allowlist.length + 1);
    expect(maximum).toBe(30);
    expect(store.size).toBe(maximum); // every combination was hit, none beyond the bound
    expect([...store.keys()].every((key) => key.startsWith(`daily:${today()}:visita:`))).toBe(true);
  });

  it("does not touch the five old events' keys", async () => {
    const { kv, store } = makeKv();
    await handleStatsRequest(eventRequest({ name: "whatsapp_click" }), { ...BASE_ENV, STATS_KV: kv });
    expect([...store.keys()]).toEqual([`daily:${today()}:whatsapp_click:`]);
  });

  it("still answers 204 when the KV write fails on a visit", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const kv = { get: vi.fn(async () => { throw new Error("kv down"); }), put: vi.fn() } as unknown as KVNamespace;
    const resp = await handleStatsRequest(visita("google", "/"), { ...BASE_ENV, STATS_KV: kv });
    expect(resp.status).toBe(204);
  });
});

describe("worker dispatch", () => {
  it("routes origenlab.cl/api/stats/* to the stats handler", async () => {
    const { kv, store } = makeKv();
    const resp = await worker.fetch(eventRequest({ name: "form_submit" }), { ...BASE_ENV, STATS_KV: kv } as ProxyEnv);
    expect(resp.status).toBe(204);
    expect(store.size).toBe(1);
  });

  it("does not let the dashboard hostname reach the stats handler or the dashboard allowlist", async () => {
    const fetchMock = stubFetch();
    const { kv, store } = makeKv();
    const resp = await worker.fetch(
      eventRequest({ name: "form_submit" }, { url: "https://dashboard.origenlab.cl/api/stats/event" }),
      { ...BASE_ENV, STATS_KV: kv } as ProxyEnv,
    );
    expect(resp.status).toBe(405); // POST not on the dashboard allowlist
    expect(store.size).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
