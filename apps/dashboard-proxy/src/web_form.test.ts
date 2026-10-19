import { afterEach, describe, expect, it, vi } from "vitest";

import { WEB_FORM_UPSTREAM_PATH, handleWebFormRequest } from "../src/web_form";
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
    expect(resp.headers.get("Location")).toBe("/contacto/?ok=1");
    expect(fetchMock).toHaveBeenCalledOnce();
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
