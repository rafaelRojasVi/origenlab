import { afterEach, describe, expect, it, vi } from "vitest";

import {
  TURNSTILE_SITEVERIFY_URL,
  WEB_FORM_UPSTREAM_PATH,
  handleWebFormRequest,
} from "../src/web_form";
import worker from "../src/index";
import {
  API_AUTH_HEADER,
  CF_ACCESS_CLIENT_ID_HEADER,
  CF_ACCESS_CLIENT_SECRET_HEADER,
  type ProxyEnv,
} from "../src/proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
  CF_ACCESS_CLIENT_ID: "cf-client-id",
  CF_ACCESS_CLIENT_SECRET: "cf-client-secret",
};

const WEB_ORIGIN = "https://origenlab.cl";

function makeJsonRequest(
  fields: Record<string, unknown>,
  opts: { origin?: string; method?: string; contentLength?: number } = {},
): Request {
  const body = JSON.stringify(fields);
  const headers = new Headers({
    "Content-Type": "application/json",
    "Origin": opts.origin ?? WEB_ORIGIN,
  });
  if (opts.contentLength !== undefined) {
    headers.set("Content-Length", String(opts.contentLength));
  }
  return new Request("https://origenlab.cl/api/web/solicitud", {
    method: opts.method ?? "POST",
    headers,
    body: opts.method === "GET" ? undefined : body,
  });
}

function makeFormRequest(
  fields: Record<string, string>,
  opts: { origin?: string } = {},
): Request {
  const body = new URLSearchParams(fields).toString();
  const headers = new Headers({
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": opts.origin ?? WEB_ORIGIN,
  });
  return new Request("https://origenlab.cl/api/web/solicitud", {
    method: "POST",
    headers,
    body,
  });
}

function stubUpstreamFetch(status = 200) {
  const fetchMock = vi.fn(async () => {
    return new Response(JSON.stringify({ status: "ok" }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("web form handler", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("refuses non-POST with 405", async () => {
    const req = makeJsonRequest({}, { method: "GET" });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(405);
  });

  it("refuses wrong Origin with 403", async () => {
    const req = makeJsonRequest({}, { origin: "https://dashboard.origenlab.cl" });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(403);
  });

  it("accepts correct Origin https://origenlab.cl without 403", async () => {
    const fetchMock = stubUpstreamFetch();
    const req = makeJsonRequest(
      { name: "Test", render_at: Math.floor(Date.now() / 1000) - 10 },
      { origin: WEB_ORIGIN },
    );
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).not.toBe(403);
    expect(fetchMock).toHaveBeenCalled();
  });

  it("refuses body > 16384 bytes with 413 via Content-Length header", async () => {
    const req = makeJsonRequest({}, { contentLength: 16_385 });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(413);
  });

  it("refuses body > 16384 bytes with 413 via actual buffer", async () => {
    const body = "x".repeat(16_385);
    const headers = new Headers({
      "Content-Type": "application/x-www-form-urlencoded",
      "Origin": WEB_ORIGIN,
    });
    const req = new Request("https://origenlab.cl/api/web/solicitud", {
      method: "POST",
      headers,
      body,
    });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(413);
  });

  it("returns 200 without forwarding when honeypot field _trap is set (JSON body)", async () => {
    const fetchMock = stubUpstreamFetch();
    const req = makeJsonRequest({ name: "Bot", _trap: "gotcha" });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(200);
    const json = await resp.json();
    expect(json).toEqual({ status: "received" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 200 without forwarding when honeypot field _trap is set (form body)", async () => {
    const fetchMock = stubUpstreamFetch();
    const req = makeFormRequest({ name: "Bot", _trap: "gotcha" });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 200 without forwarding when render_at is less than 3 seconds ago", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:05.000Z"));
    const fetchMock = stubUpstreamFetch();
    // render_at = 1 second ago
    const renderAt = Math.floor(Date.now() / 1000) - 1;
    const req = makeJsonRequest({ name: "Fast Bot", render_at: renderAt });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(200);
    const json = await resp.json();
    expect(json).toEqual({ status: "received" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards valid submission to upstream with API key and CF Access headers", async () => {
    const fetchMock = stubUpstreamFetch(201);
    const renderAt = Math.floor(Date.now() / 1000) - 10;
    const req = makeJsonRequest({
      name: "Test User",
      email: "test@example.com",
      render_at: renderAt,
    });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(201);
    expect(fetchMock).toHaveBeenCalledOnce();

    const [upstreamReq] = fetchMock.mock.calls[0] as unknown as [Request];
    const upstreamUrl = typeof upstreamReq === "string" ? upstreamReq : upstreamReq.url;
    expect(upstreamUrl).toContain(WEB_FORM_UPSTREAM_PATH);
    expect(upstreamUrl).toContain("api.origenlab.cl");

    const headers =
      typeof upstreamReq === "string"
        ? new Headers()
        : (upstreamReq as Request).headers;
    expect(headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBe("cf-client-id");
    expect(headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBe("cf-client-secret");
  });

  it("forwards valid form-encoded submission to upstream and returns 303 (no-JS path)", async () => {
    const fetchMock = stubUpstreamFetch();
    const renderAt = String(Math.floor(Date.now() / 1000) - 10);
    const req = makeFormRequest({ name: "User", email: "u@example.com", render_at: renderAt });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    // no-JS path: form-encoded → Worker returns 303 after successful upstream call
    expect(resp.status).toBe(303);
    expect(resp.headers.get("Location")).toBe("https://origenlab.cl/contacto/enviado/");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("no-JS success redirects to /contacto/enviado/ for a browser form post", async () => {
    stubUpstreamFetch();
    const renderAt = String(Math.floor(Date.now() / 1000) - 10);
    const req = new Request("https://origenlab.cl/api/web/solicitud", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": WEB_ORIGIN,
        "Accept": "text/html",
      },
      body: new URLSearchParams({
        need: "sonicador",
        email: "ana@ficticio.invalid",
        render_at: renderAt,
      }).toString(),
    });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(303);
    expect(resp.headers.get("Location")).toBe("https://origenlab.cl/contacto/enviado/");
  });

  it.each([
    ["an empty render_at", { render_at: "" }],
    ["no render_at field at all", {}],
    ["an invalid render_at", { render_at: "not-a-time" }],
  ])("refuses a no-JS post with %s (400) and forwards nothing", async (_label, extra) => {
    const fetchMock = stubUpstreamFetch();
    const req = makeFormRequest({ need: "sonicador", email: "ana@ficticio.invalid", org: "Instituto Ficticio", ...extra });
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(400);
    expect(await resp.json()).toMatchObject({ error: { code: "render_at_required" } });
    expect(resp.headers.get("Location")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not redirect when the upstream call fails (no-JS path)", async () => {
    stubUpstreamFetch(422);
    const renderAt = String(Math.floor(Date.now() / 1000) - 10);
    const resp = await handleWebFormRequest(
      makeFormRequest({ email: "ana@ficticio.invalid", render_at: renderAt }),
      TEST_ENV,
    );
    expect(resp.status).toBe(422);
    expect(resp.headers.get("Location")).toBeNull();
  });

  describe("product and consent_version pass-through", () => {
    const EXTRA = { product: "UP400St", consent_version: "consent-v1-2026-11" };

    async function forwardedBody(req: Request): Promise<Record<string, string>> {
      const fetchMock = stubUpstreamFetch();
      await handleWebFormRequest(req, TEST_ENV);
      expect(fetchMock).toHaveBeenCalledOnce();
      const [upstreamReq] = fetchMock.mock.calls[0] as unknown as [Request];
      return JSON.parse(await upstreamReq.text());
    }

    const base = () => ({
      submission_id: crypto.randomUUID(),
      need: "sonicador",
      email: "ana@ficticio.invalid",
      org: "Instituto Ficticio",
      render_at: String(Math.floor(Date.now() / 1000) - 10),
    });

    it("forwards both fields unchanged from a JSON body", async () => {
      const parsed = await forwardedBody(makeJsonRequest({ ...base(), ...EXTRA }));
      expect(parsed.product).toBe(EXTRA.product);
      expect(parsed.consent_version).toBe(EXTRA.consent_version);
    });

    it("forwards both fields unchanged from a urlencoded body", async () => {
      const parsed = await forwardedBody(makeFormRequest({ ...base(), ...EXTRA }));
      expect(parsed.product).toBe(EXTRA.product);
      expect(parsed.consent_version).toBe(EXTRA.consent_version);
    });

    it("forwards both fields unchanged from a multipart body", async () => {
      const fd = new FormData();
      for (const [k, v] of Object.entries({ ...base(), ...EXTRA })) fd.append(k, v);
      const req = new Request("https://origenlab.cl/api/web/solicitud", {
        method: "POST",
        headers: { Origin: WEB_ORIGIN },
        body: fd,
      });
      const parsed = await forwardedBody(req);
      expect(parsed.product).toBe(EXTRA.product);
      expect(parsed.consent_version).toBe(EXTRA.consent_version);
    });
  });

  it("proceeds without forwarding operator session cookies", async () => {
    const fetchMock = stubUpstreamFetch();
    const renderAt = Math.floor(Date.now() / 1000) - 10;
    const headers = new Headers({
      "Content-Type": "application/json",
      "Origin": WEB_ORIGIN,
      "Cookie": "session=abc123; ol_auth=secret",
    });
    const req = new Request("https://origenlab.cl/api/web/solicitud", {
      method: "POST",
      headers,
      body: JSON.stringify({ name: "Test", render_at: renderAt }),
    });
    await handleWebFormRequest(req, TEST_ENV);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [upstreamReq] = fetchMock.mock.calls[0] as unknown as [Request];
    const sentHeaders = (upstreamReq as Request).headers;
    expect(sentHeaders.get("Cookie")).toBeNull();
  });

  it("returns 429 when rate limit binding denies request", async () => {
    const fetchMock = stubUpstreamFetch();
    const renderAt = Math.floor(Date.now() / 1000) - 10;
    const req = makeJsonRequest({ name: "Test", render_at: renderAt });
    const envWithRateLimit = {
      ...TEST_ENV,
      WEB_FORM_RATE_LIMIT: {
        limit: vi.fn(async () => ({ success: false })),
      },
    };
    const resp = await handleWebFormRequest(req, envWithRateLimit);
    expect(resp.status).toBe(429);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("proceeds normally when rate limit binding allows request", async () => {
    const fetchMock = stubUpstreamFetch();
    const renderAt = Math.floor(Date.now() / 1000) - 10;
    const req = makeJsonRequest({ name: "Test", render_at: renderAt });
    const envWithRateLimit = {
      ...TEST_ENV,
      WEB_FORM_RATE_LIMIT: {
        limit: vi.fn(async () => ({ success: true })),
      },
    };
    const resp = await handleWebFormRequest(req, envWithRateLimit);
    expect(resp.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("proceeds without rate limit binding (binding absent → WAF handles it)", async () => {
    const fetchMock = stubUpstreamFetch();
    const renderAt = Math.floor(Date.now() / 1000) - 10;
    const req = makeJsonRequest({ name: "Test", render_at: renderAt });
    // No WEB_FORM_RATE_LIMIT on env
    const resp = await handleWebFormRequest(req, TEST_ENV);
    expect(resp.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("web form render_at is required", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ["missing", undefined],
    ["null", null],
    ["empty", ""],
    ["blank", "   "],
    ["not a time", "not-a-time"],
    ["zero", 0],
    ["negative", -5],
    ["Infinity", "Infinity"],
    ["a boolean", true],
    ["an object", { t: 1 }],
  ])("refuses a JSON submission whose render_at is %s (400, nothing forwarded)", async (_label, renderAt) => {
    const fetchMock = stubUpstreamFetch();
    const fields: Record<string, unknown> = { need: "sonicador", email: "ana@ficticio.invalid", org: "Instituto Ficticio" };
    if (renderAt !== undefined) fields.render_at = renderAt;
    const resp = await handleWebFormRequest(makeJsonRequest(fields), TEST_ENV);
    expect(resp.status).toBe(400);
    expect(await resp.json()).toEqual({
      error: { code: "render_at_required", message: "Escríbanos directamente a contacto@origenlab.cl" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards render_at as a number, from a float string or an ISO string", async () => {
    for (const [raw, expected] of [
      ["1767268800.5", 1767268800.5],
      ["2026-01-01T12:00:00.000Z", 1767268800],
    ] as const) {
      const fetchMock = stubUpstreamFetch();
      await handleWebFormRequest(makeJsonRequest({ email: "ana@ficticio.invalid", render_at: raw }), TEST_ENV);
      const [upstreamReq] = fetchMock.mock.calls[0] as unknown as [Request];
      expect(JSON.parse(await upstreamReq.text()).render_at).toBe(expected);
    }
  });
});

describe("web form JSON body that is not an object", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each(["null", '"x"', "[]", "[1,2]", "7", "true", "{not json"])(
    "treats %s as an empty submission: a 400, never an exception (Cloudflare 1101)",
    async (raw) => {
      const fetchMock = stubUpstreamFetch();
      const req = new Request("https://origenlab.cl/api/web/solicitud", {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: WEB_ORIGIN },
        body: raw,
      });
      const resp = await worker.fetch(req, TEST_ENV);
      expect(resp.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});

describe("web form Turnstile", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const SECRET_ENV = { ...TEST_ENV, TURNSTILE_SECRET_KEY: "test-turnstile-key" };
  const TOKEN = "XXXX.DUMMY.TOKEN.XXXX";
  const fields = (extra: Record<string, unknown> = {}) => ({
    submission_id: crypto.randomUUID(),
    need: "sonicador",
    email: "ana@ficticio.invalid",
    org: "Instituto Ficticio",
    render_at: Math.floor(Date.now() / 1000) - 10,
    "cf-turnstile-response": TOKEN,
    ...extra,
  });

  /** siteverify answers `verdict` (or fails); the API answers 200. */
  function stubFetches(verdict: Record<string, unknown> | "network" | number) {
    const fetchMock = vi.fn(async (input: Request | string, _init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.url;
      if (url === TURNSTILE_SITEVERIFY_URL) {
        if (verdict === "network") throw new Error("down");
        if (typeof verdict === "number") return new Response("nope", { status: verdict });
        return new Response(JSON.stringify(verdict), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ status: "sent" }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const calls = (url: string) =>
      fetchMock.mock.calls.filter(([input]) => (typeof input === "string" ? input : input.url) === url);
    return { fetchMock, calls };
  }
  const GOOD_VERDICT = { success: true, hostname: "origenlab.cl", action: "solicitud" };
  const apiUrl = `https://api.origenlab.cl${WEB_FORM_UPSTREAM_PATH}`;

  it("is off without the secret: no siteverify call, and the token never reaches the API", async () => {
    const { fetchMock, calls } = stubFetches(GOOD_VERDICT);
    const resp = await handleWebFormRequest(makeJsonRequest(fields()), TEST_ENV);
    expect(resp.status).toBe(200);
    expect(calls(TURNSTILE_SITEVERIFY_URL)).toHaveLength(0);
    expect(fetchMock).toHaveBeenCalledOnce();
    const forwarded = JSON.parse(await (fetchMock.mock.calls[0][0] as Request).text());
    expect("cf-turnstile-response" in forwarded).toBe(false);
    expect("_trap" in forwarded).toBe(false);
  });

  it("is off with a blank secret too", async () => {
    const { calls } = stubFetches(GOOD_VERDICT);
    const resp = await handleWebFormRequest(makeJsonRequest(fields({ "cf-turnstile-response": undefined })), {
      ...TEST_ENV,
      TURNSTILE_SECRET_KEY: "  ",
    });
    expect(resp.status).toBe(200);
    expect(calls(TURNSTILE_SITEVERIFY_URL)).toHaveLength(0);
  });

  it("with the secret, verifies the token server-side and forwards without it", async () => {
    const { calls } = stubFetches(GOOD_VERDICT);
    const req = makeJsonRequest(fields());
    req.headers.set("CF-Connecting-IP", "203.0.113.7");
    const resp = await handleWebFormRequest(req, SECRET_ENV);
    expect(resp.status).toBe(200);
    const [verify] = calls(TURNSTILE_SITEVERIFY_URL);
    const sent = new URLSearchParams(String((verify[1] as RequestInit).body));
    expect(sent.get("secret")).toBe("test-turnstile-key");
    expect(sent.get("response")).toBe(TOKEN);
    expect(sent.get("remoteip")).toBe("203.0.113.7");
    const [forward] = calls(apiUrl);
    const forwarded = JSON.parse(await (forward[0] as Request).text());
    expect("cf-turnstile-response" in forwarded).toBe(false);
    expect(JSON.stringify(forwarded)).not.toContain("test-turnstile-key");
  });

  it("with the secret, verifies a token sent by a form post too", async () => {
    const { calls } = stubFetches(GOOD_VERDICT);
    const resp = await handleWebFormRequest(
      makeFormRequest({ email: "ana@ficticio.invalid", render_at: String(Math.floor(Date.now() / 1000) - 10), "cf-turnstile-response": TOKEN }),
      SECRET_ENV,
    );
    expect(resp.status).toBe(303);
    expect(calls(TURNSTILE_SITEVERIFY_URL)).toHaveLength(1);
  });

  it.each([
    ["missing", undefined],
    ["empty", ""],
    ["not a string", 12345],
    ["oversized", "t".repeat(2049)],
  ])("with the secret, refuses a %s token (403) without calling siteverify or the API", async (_label, token) => {
    const { fetchMock } = stubFetches(GOOD_VERDICT);
    const resp = await handleWebFormRequest(makeJsonRequest(fields({ "cf-turnstile-response": token })), SECRET_ENV);
    expect(resp.status).toBe(403);
    expect(await resp.json()).toMatchObject({ error: { code: "challenge_required" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["a failed challenge", { success: false, "error-codes": ["invalid-input-response"] }],
    ["another site's token", { success: true, hostname: "example.invalid", action: "solicitud" }],
    ["another action's token", { success: true, hostname: "origenlab.cl", action: "login" }],
    ["a verdict without success", { hostname: "origenlab.cl", action: "solicitud" }],
  ])("with the secret, refuses %s (403) and forwards nothing", async (_label, verdict) => {
    const { calls } = stubFetches(verdict);
    const resp = await handleWebFormRequest(makeJsonRequest(fields()), SECRET_ENV);
    expect(resp.status).toBe(403);
    expect(await resp.json()).toMatchObject({ error: { code: "challenge_failed" } });
    expect(calls(apiUrl)).toHaveLength(0);
  });

  it.each([["unreachable", "network" as const], ["an error status", 500]])(
    "with the secret, fails closed when siteverify is %s (503)",
    async (_label, verdict) => {
      const { calls } = stubFetches(verdict);
      const resp = await handleWebFormRequest(makeJsonRequest(fields()), SECRET_ENV);
      expect(resp.status).toBe(503);
      expect(await resp.json()).toMatchObject({ error: { code: "challenge_unavailable" } });
      expect(calls(apiUrl)).toHaveLength(0);
    },
  );

  it("still drops the honeypot and the 3-second bot before spending a siteverify call", async () => {
    const { fetchMock } = stubFetches(GOOD_VERDICT);
    expect((await handleWebFormRequest(makeJsonRequest(fields({ _trap: "x" })), SECRET_ENV)).status).toBe(200);
    expect(
      (await handleWebFormRequest(makeJsonRequest(fields({ render_at: Date.now() / 1000 })), SECRET_ENV)).status,
    ).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
