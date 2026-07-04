import { afterEach, describe, expect, it, vi } from "vitest";

import { handleRequest } from "../src/index";
import {
  API_AUTH_HEADER,
  CF_ACCESS_CLIENT_ID_HEADER,
  CF_ACCESS_CLIENT_SECRET_HEADER,
  buildUpstreamHeaders,
  type ProxyEnv,
} from "../src/proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
  CF_ACCESS_CLIENT_ID: "cf-client-id",
  CF_ACCESS_CLIENT_SECRET: "cf-client-secret",
};

describe("buildUpstreamHeaders", () => {
  it("injects API key and Cloudflare Access service-token headers when both CF secrets are set", () => {
    const incoming = new Headers({ Accept: "application/json", "X-Request-ID": "req-abc" });
    const headers = buildUpstreamHeaders(TEST_ENV, incoming);
    expect(headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBe("cf-client-id");
    expect(headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBe("cf-client-secret");
    expect(headers.get("X-Request-ID")).toBe("req-abc");
  });

  it("does not send partial Cloudflare Access headers when only client id is set", () => {
    const headers = buildUpstreamHeaders(
      { ...TEST_ENV, CF_ACCESS_CLIENT_SECRET: "" },
      new Headers({ Accept: "application/json" }),
    );
    expect(headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBeNull();
    expect(headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBeNull();
  });

  it("does not send partial Cloudflare Access headers when only client secret is set", () => {
    const headers = buildUpstreamHeaders(
      { ...TEST_ENV, CF_ACCESS_CLIENT_ID: "" },
      new Headers({ Accept: "application/json" }),
    );
    expect(headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBeNull();
    expect(headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBeNull();
  });
});

describe("handleRequest", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GET /api/operator/status forwards upstream with X-OriginLab-API-Key", async () => {
    const captured: { url: string; headers: Headers; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (req: Request) => {
        captured.push({
          url: req.url,
          headers: req.headers,
          method: req.method,
        });
        return new Response(JSON.stringify({ verdict: "OK" }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "X-Request-ID": "upstream-req-1",
          },
        });
      }),
    );

    const response = await handleRequest(
      new Request("https://dashboard.origenlab.cl/api/operator/status", {
        method: "GET",
        headers: { Accept: "application/json", "X-Request-ID": "browser-req-1" },
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(captured).toHaveLength(1);
    expect(captured[0]?.url).toBe("https://api.origenlab.cl/operator/status");
    expect(captured[0]?.method).toBe("GET");
    expect(captured[0]?.headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(captured[0]?.headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBe("cf-client-id");
    expect(captured[0]?.headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBe("cf-client-secret");
    expect(response.headers.get("X-Request-ID")).toBe("upstream-req-1");

    const bodyText = await response.text();
    expect(bodyText).not.toContain("server-only-token");
    expect(bodyText).not.toContain("cf-client-secret");
    expect(bodyText).not.toContain("cf-client-id");
  });

  it("POST is rejected with 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      new Request("https://dashboard.origenlab.cl/api/operator/status", { method: "POST" }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("unknown upstream path is rejected", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      new Request("https://dashboard.origenlab.cl/api/emails", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("OPTIONS returns 204 without upstream fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      new Request("https://dashboard.origenlab.cl/api/health", { method: "OPTIONS" }),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
