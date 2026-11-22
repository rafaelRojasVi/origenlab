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

/** A Workers Analytics Engine dataset double: records every data point written. */
function makeAe() {
  const points: AnalyticsEngineDataPoint[] = [];
  const write = vi.fn((point?: AnalyticsEngineDataPoint) => {
    if (point) points.push(point);
  });
  const ae = { writeDataPoint: write } as unknown as AnalyticsEngineDataset;
  return { ae, write, points };
}

/** What a `SUM(_sample_interval) … GROUP BY blob1..blob4` query would return, as "a|b|c|d" → total. */
function tally(points: AnalyticsEngineDataPoint[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const point of points) {
    const key = (point.blobs ?? []).join("|");
    out.set(key, (out.get(key) ?? 0) + (point.doubles?.[0] ?? 0));
  }
  return out;
}

function statsEnv(extra: Record<string, unknown> = {}) {
  return { ...BASE_ENV, ...extra };
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
    // The script route went with the unused proxy mode: there is no GET at all.
    ["GET", SCRIPT_URL],
    ["GET", `${SCRIPT_URL}?x=1`],
  ])("%s %s is 404 and never reaches upstream", async (method, url) => {
    const fetchMock = stubFetch();
    const { ae, write } = makeAe();
    const resp = await handleStatsRequest(eventRequest(undefined, { method, url }), statsEnv({ STATS_AE: ae }));
    expect(resp.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("returns 404 with no body and no cookies", async () => {
    const resp = await handleStatsRequest(eventRequest(undefined, { method: "DELETE" }), statsEnv());
    expect(await resp.text()).toBe("");
    expect(resp.headers.get("Set-Cookie")).toBeNull();
  });
});

describe("stats event guards", () => {
  it("refuses a missing Origin with 403", async () => {
    const fetchMock = stubFetch();
    const resp = await handleStatsRequest(eventRequest(undefined, { origin: null }), statsEnv());
    expect(resp.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["https://dashboard.origenlab.cl", "https://evil.example.invalid", "http://origenlab.cl", "null"])(
    "refuses Origin %s with 403",
    async (origin) => {
      const fetchMock = stubFetch();
      const resp = await handleStatsRequest(eventRequest(undefined, { origin }), statsEnv());
      expect(resp.status).toBe(403);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("refuses Sec-Fetch-Site: cross-site with 403", async () => {
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "Sec-Fetch-Site": "cross-site" } }),
      statsEnv(),
    );
    expect(resp.status).toBe(403);
  });

  it("refuses a form content type with 415", async () => {
    const resp = await handleStatsRequest(
      eventRequest(undefined, { contentType: "application/x-www-form-urlencoded", raw: "name=whatsapp_click" }),
      statsEnv(),
    );
    expect(resp.status).toBe(415);
  });

  it("accepts text/plain, which is what sendBeacon sends for a string", async () => {
    const fetchMock = stubFetch();
    const { ae, write } = makeAe();
    const resp = await handleStatsRequest(
      eventRequest(undefined, { contentType: "text/plain;charset=UTF-8" }),
      statsEnv({ STATS_AE: ae }),
    );
    expect(resp.status).toBe(204);
    expect(write).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a declared Content-Length over the cap with 413", async () => {
    const fetchMock = stubFetch();
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "Content-Length": String(STATS_BODY_LIMIT + 1) } }),
      statsEnv(),
    );
    expect(resp.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an actual body over the cap with 413", async () => {
    const fetchMock = stubFetch();
    const big = { name: "cotizacion_click", props: { modelo: "x" }, pad: "p".repeat(STATS_BODY_LIMIT) };
    const resp = await handleStatsRequest(eventRequest(big), statsEnv());
    expect(resp.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses invalid JSON with 400", async () => {
    const resp = await handleStatsRequest(eventRequest(undefined, { raw: "{not json" }), statsEnv());
    expect(resp.status).toBe(400);
  });

  it.each([null, [], "cotizacion_click", 7])("refuses a non-object body %j with 400", async (body) => {
    const resp = await handleStatsRequest(eventRequest(body), statsEnv());
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
    const { ae, write } = makeAe();
    const resp = await handleStatsRequest(eventRequest(body), statsEnv({ STATS_AE: ae }));
    expect(resp.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
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
    const resp = await handleStatsRequest(eventRequest(body), statsEnv());
    expect(resp.status).toBe(400);
  });

  it("knows exactly the six event names", () => {
    expect([...STATS_EVENT_NAMES].sort()).toEqual(
      ["3d_interact", "cotizacion_click", "form_submit", "pdf_download", "visita", "whatsapp_click"].sort(),
    );
  });

  it("knows exactly the seven visit origins", () => {
    expect([...STATS_ORIGENES].sort()).toEqual(
      ["directo", "google", "linkedin", "otro", "perfil-google", "whatsapp", "interno"].sort(),
    );
  });

  it.each(["cotizacion_click", "whatsapp_click", "pdf_download", "3d_interact", "form_submit"])(
    "%s still refuses origen and pagina with 400",
    async (name) => {
      const fetchMock = stubFetch();
      const { ae, write } = makeAe();
      for (const props of [{ origen: "google" }, { pagina: "/catalogo" }, { modelo: "UP400St", origen: "google", pagina: "/" }]) {
        const resp = await handleStatsRequest(eventRequest({ name, props }), statsEnv({ STATS_AE: ae }));
        expect(resp.status).toBe(400);
      }
      expect(fetchMock).not.toHaveBeenCalled();
      expect(write).not.toHaveBeenCalled();
    },
  );
});

describe("stats visita event", () => {
  const ORIGENES = ["google", "perfil-google", "linkedin", "directo", "whatsapp", "otro", "interno"];

  it.each(ORIGENES)("accepts origen %s", async (origen) => {
    const resp = await handleStatsRequest(eventRequest({ name: "visita", props: { origen, pagina: "/catalogo/ultrasonido" } }), statsEnv());
    expect(resp.status).toBe(204);
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
    const { ae, write } = makeAe();
    const resp = await handleStatsRequest(eventRequest({ name: "visita", props }), statsEnv({ STATS_AE: ae }));
    expect(resp.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("accepts the root path and a path of exactly 81 characters", async () => {
    for (const pagina of ["/", "/" + "a".repeat(80)]) {
      const resp = await handleStatsRequest(eventRequest({ name: "visita", props: { origen: "directo", pagina } }), statsEnv());
      expect(resp.status).toBe(204);
    }
  });

  it("records only {origen, pagina}: any other prop is dropped and nothing is forwarded", async () => {
    const fetchMock = stubFetch();
    const { ae, points } = makeAe();
    await handleStatsRequest(
      eventRequest({ name: "visita", props: { origen: "linkedin", pagina: "/contacto", email: "a@b.invalid" } }),
      statsEnv({ STATS_AE: ae, STATS_PAGINAS: "/contacto" }),
    );
    expect(points).toEqual([{ blobs: ["visita", "", "linkedin", "/contacto"], doubles: [1], indexes: ["visita"] }]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("stats rate limit", () => {
  it("returns 429 when the limiter says no, before upstream or counter", async () => {
    const fetchMock = stubFetch();
    const { ae, write } = makeAe();
    const limit = vi.fn(async (_o: { key: string }) => ({ success: false }));
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "CF-Connecting-IP": "203.0.113.9" } }),
      statsEnv({ STATS_RATELIMIT: { limit }, STATS_AE: ae }),
    );
    expect(resp.status).toBe(429);
    expect(limit).toHaveBeenCalledWith({ key: "203.0.113.9" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("lets the request through when the limiter allows it", async () => {
    const limit = vi.fn(async (_o: { key: string }) => ({ success: true }));
    const resp = await handleStatsRequest(eventRequest(), statsEnv({ STATS_RATELIMIT: { limit } }));
    expect(resp.status).toBe(204);
  });

  it("does not rate-limit refused requests (wrong origin never spends a token)", async () => {
    const limit = vi.fn(async (_o: { key: string }) => ({ success: true }));
    await handleStatsRequest(eventRequest(undefined, { origin: "https://evil.example.invalid" }), statsEnv({ STATS_RATELIMIT: { limit } }));
    expect(limit).not.toHaveBeenCalled();
  });
});

describe("stats counter on Analytics Engine", () => {
  it("is the only mode: a leftover STATS_ORIGIN neither forwards an event nor serves a script", async () => {
    const fetchMock = stubFetch(200, "console.log(1)");
    const { ae, write } = makeAe();
    const env = { ...BASE_ENV, STATS_ORIGIN: "https://stats.example.invalid", STATS_AE: ae } as ProxyEnv;
    const event = await handleStatsRequest(eventRequest(), env);
    expect(event.status).toBe(204);
    expect(write).toHaveBeenCalledOnce();
    const script = await handleStatsRequest(new Request(SCRIPT_URL), env);
    expect(script.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("writes one data point per event and answers 204 with no body", async () => {
    const fetchMock = stubFetch();
    const { ae, write } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_MODELOS: "UP400St, UP200St" };

    const first = await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);
    expect(first.status).toBe(204);
    expect(await first.text()).toBe("");
    await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);

    expect(write).toHaveBeenCalledTimes(2);
    expect(write).toHaveBeenLastCalledWith({
      blobs: ["pdf_download", "UP400St", "", ""],
      doubles: [1],
      indexes: ["pdf_download"],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never reads before writing: concurrent events each land", async () => {
    const { ae, write, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae };
    await Promise.all(Array.from({ length: 20 }, () => handleStatsRequest(eventRequest({ name: "form_submit" }), env)));
    expect(write).toHaveBeenCalledTimes(20);
    expect(tally(points).get("form_submit|||")).toBe(20);
  });

  it("records name and modelo, with an empty modelo when none is given", async () => {
    const { ae, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_MODELOS: "UP400St,UP200St" };
    await handleStatsRequest(eventRequest({ name: "form_submit" }), env);
    await handleStatsRequest(eventRequest({ name: "form_submit", props: {} }), env);
    await handleStatsRequest(eventRequest({ name: "3d_interact", props: { modelo: "UP200St" } }), env);
    expect([...tally(points).entries()].sort()).toEqual(
      [
        ["3d_interact|UP200St||", 1],
        ["form_submit|||", 2],
      ].sort(),
    );
  });

  it("keeps an allowlisted modelo and collapses everything else to \"other\"", async () => {
    const { ae, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_MODELOS: "UP400St,UP200St" };
    for (const modelo of ["UP400St", "rnd-9f3a1", "rnd-77c0e", "rnd-0b2d4"]) {
      await handleStatsRequest(eventRequest({ name: "whatsapp_click", props: { modelo } }), env);
    }
    expect([...tally(points).entries()].sort()).toEqual(
      [
        ["whatsapp_click|UP400St||", 1],
        ["whatsapp_click|other||", 3],
      ].sort(),
    );
  });

  it("collapses every modelo to \"other\" when STATS_MODELOS is empty or unset", async () => {
    for (const modelos of [undefined, "", " , "]) {
      const { ae, points } = makeAe();
      const env = { ...BASE_ENV, STATS_AE: ae, STATS_MODELOS: modelos };
      await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "UP400St" } }), env);
      await handleStatsRequest(eventRequest({ name: "pdf_download", props: { modelo: "anything-else" } }), env);
      expect([...tally(points).keys()]).toEqual(["pdf_download|other||"]);
    }
  });

  it("still answers 204 when writeDataPoint throws, and logs no personal data", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const ae = { writeDataPoint: vi.fn(() => { throw new Error("ae down"); }) } as unknown as AnalyticsEngineDataset;
    const resp = await handleStatsRequest(
      eventRequest(undefined, { headers: { "CF-Connecting-IP": "203.0.113.9", "User-Agent": "UA/1.0" } }),
      { ...BASE_ENV, STATS_AE: ae },
    );
    expect(resp.status).toBe(204);
    const logged = JSON.stringify([...warn.mock.calls, ...error.mock.calls]);
    expect(logged).not.toContain("203.0.113.9");
    expect(logged).not.toContain("UA/1.0");
  });

  it("answers 204 without writing anything when no dataset is bound", async () => {
    const resp = await handleStatsRequest(eventRequest(), { ...BASE_ENV });
    expect(resp.status).toBe(204);
    const visit = await handleStatsRequest(eventRequest({ name: "visita", props: { origen: "interno", pagina: "/" } }), { ...BASE_ENV });
    expect(visit.status).toBe(204);
  });

  it("does not write an event that failed validation", async () => {
    const { ae, write } = makeAe();
    const resp = await handleStatsRequest(eventRequest({ name: "pageview" }), { ...BASE_ENV, STATS_AE: ae });
    expect(resp.status).toBe(400);
    expect(write).not.toHaveBeenCalled();
  });
});

describe("stats visita counter", () => {
  const visita = (origen: string, pagina: string) => eventRequest({ name: "visita", props: { origen, pagina } });

  it("writes one data point per visit with origen and an allowlisted page", async () => {
    const { ae, write, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: "/, /catalogo ,/contacto" };
    await handleStatsRequest(visita("google", "/catalogo"), env);
    await handleStatsRequest(visita("google", "/catalogo"), env);
    await handleStatsRequest(visita("google", "/"), env);
    expect(write).toHaveBeenCalledTimes(3);
    expect(write).toHaveBeenLastCalledWith({ blobs: ["visita", "", "google", "/"], doubles: [1], indexes: ["visita"] });
    expect([...tally(points).entries()].sort()).toEqual(
      [
        ["visita||google|/catalogo", 2],
        ["visita||google|/", 1],
      ].sort(),
    );
  });

  it("records a same-site navigation as origen «interno»", async () => {
    const { ae, write } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: "/contacto" };
    const resp = await handleStatsRequest(visita("interno", "/contacto"), env);
    expect(resp.status).toBe(204);
    expect(write).toHaveBeenCalledWith({ blobs: ["visita", "", "interno", "/contacto"], doubles: [1], indexes: ["visita"] });
  });

  it("collapses every page outside STATS_PAGINAS to \"otra\"", async () => {
    const { ae, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: "/catalogo" };
    for (const pagina of ["/rnd-9f3a1", "/rnd-77c0e", "/catalogo/ultrasonido"]) {
      await handleStatsRequest(visita("linkedin", pagina), env);
    }
    expect([...tally(points).entries()]).toEqual([["visita||linkedin|otra", 3]]);
  });

  it("matches the page list exactly: no prefix, no trailing-slash folding", async () => {
    const { ae, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: "/catalogo" };
    await handleStatsRequest(visita("directo", "/catalogo/"), env);
    expect([...tally(points).keys()]).toEqual(["visita||directo|otra"]);
  });

  it("collapses every page to \"otra\" when STATS_PAGINAS is empty or unset", async () => {
    for (const paginas of [undefined, "", " , "]) {
      const { ae, points } = makeAe();
      const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: paginas };
      await handleStatsRequest(visita("whatsapp", "/"), env);
      await handleStatsRequest(visita("whatsapp", "/catalogo"), env);
      expect([...tally(points).entries()]).toEqual([["visita||whatsapp|otra", 2]]);
    }
  });

  it("ignores malformed STATS_PAGINAS entries instead of recording them", async () => {
    const { ae, points } = makeAe();
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: "/Catalogo,catalogo,/a?b,/ok" };
    await handleStatsRequest(visita("google", "/ok"), env);
    expect([...tally(points).keys()]).toEqual(["visita||google|/ok"]);
  });

  it("keeps the distinct (origen, pagina) values within 7 origins x (allowlist + 1), whatever the client sends", async () => {
    const { ae, points } = makeAe();
    const allowlist = ["/", "/catalogo", "/contacto", "/nosotros"];
    const env = { ...BASE_ENV, STATS_AE: ae, STATS_PAGINAS: allowlist.join(",") };
    const pages = [...allowlist, ...Array.from({ length: 25 }, (_, i) => `/rnd-${i}`)];
    for (const origen of STATS_ORIGENES) {
      for (const pagina of pages) await handleStatsRequest(visita(origen, pagina), env);
    }
    const maximum = STATS_ORIGENES.size * (allowlist.length + 1);
    expect(maximum).toBe(35);
    const groups = tally(points);
    expect(groups.size).toBe(maximum); // every combination was hit, none beyond the bound
    expect([...groups.keys()].every((key) => key.startsWith("visita||"))).toBe(true);
  });

  it("leaves origen and pagina empty on the five click events", async () => {
    const { ae, points } = makeAe();
    await handleStatsRequest(eventRequest({ name: "whatsapp_click" }), { ...BASE_ENV, STATS_AE: ae });
    expect(points).toEqual([{ blobs: ["whatsapp_click", "", "", ""], doubles: [1], indexes: ["whatsapp_click"] }]);
  });

  it("still answers 204 when the write fails on a visit", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const ae = { writeDataPoint: vi.fn(() => { throw new Error("ae down"); }) } as unknown as AnalyticsEngineDataset;
    const resp = await handleStatsRequest(visita("google", "/"), { ...BASE_ENV, STATS_AE: ae });
    expect(resp.status).toBe(204);
  });
});

describe("worker dispatch", () => {
  it("routes origenlab.cl/api/stats/* to the stats handler", async () => {
    const { ae, write } = makeAe();
    const resp = await worker.fetch(eventRequest({ name: "form_submit" }), { ...BASE_ENV, STATS_AE: ae } as ProxyEnv);
    expect(resp.status).toBe(204);
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("does not let the dashboard hostname reach the stats handler or the dashboard allowlist", async () => {
    const fetchMock = stubFetch();
    const { ae, write } = makeAe();
    const resp = await worker.fetch(
      eventRequest({ name: "form_submit" }, { url: "https://dashboard.origenlab.cl/api/stats/event" }),
      { ...BASE_ENV, STATS_AE: ae } as ProxyEnv,
    );
    expect(resp.status).toBe(405); // POST not on the dashboard allowlist
    expect(write).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
