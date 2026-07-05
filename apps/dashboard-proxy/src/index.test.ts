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

const PRODUCTION_ORIGIN = "https://dashboard.origenlab.cl";

function requestWithOrigin(
  url: string,
  init: RequestInit & { origin?: string } = {},
): Request {
  const { origin = PRODUCTION_ORIGIN, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (origin) {
    headers.set("Origin", origin);
  }
  return new Request(url, { ...rest, headers });
}

function stubUpstreamFetch(body: string = JSON.stringify({ status: "ok" })) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": "upstream-req-1",
        },
      });
    }),
  );
}

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
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", {
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

  it("GET /api/health from allowed Origin returns Access-Control-Allow-Origin", async () => {
    stubUpstreamFetch(JSON.stringify({ status: "ok" }));

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
    expect(response.headers.get("Vary")).toBe("Origin");
  });

  it("GET response includes Access-Control-Allow-Credentials: true for allowed Origin", async () => {
    stubUpstreamFetch();

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.headers.get("Access-Control-Allow-Credentials")).toBe("true");
    expect(response.headers.get("Access-Control-Allow-Methods")).toBe("GET, HEAD, OPTIONS");
    expect(response.headers.get("Access-Control-Allow-Headers")).toBe(
      "Accept, Content-Type, X-Request-ID",
    );
    expect(response.headers.get("Access-Control-Expose-Headers")).toBe("X-Request-ID");
  });

  it("OPTIONS /api/operator/automation-status returns 204 and CORS headers", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/automation-status", {
        method: "OPTIONS",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("Access-Control-Allow-Credentials")).toBe("true");
    expect(response.headers.get("Access-Control-Allow-Methods")).toBe("GET, HEAD, OPTIONS");
    expect(response.headers.get("Vary")).toBe("Origin");
  });

  it("disallowed Origin does not get Access-Control-Allow-Origin", async () => {
    stubUpstreamFetch();

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", {
        method: "GET",
        origin: "https://evil.example.com",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBeNull();
    expect(response.headers.get("Access-Control-Allow-Credentials")).toBeNull();
    expect(response.headers.get("Vary")).toBe("Origin");
  });

  it("Access-Control-Allow-Origin is never * on success or error responses", async () => {
    stubUpstreamFetch();

    const success = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );
    expect(success.headers.get("Access-Control-Allow-Origin")).not.toBe("*");

    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const forbidden = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/emails", { method: "GET" }),
      TEST_ENV,
    );
    expect(forbidden.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
  });

  it("mutating methods remain 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
      const response = await handleRequest(
        requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", { method }),
        TEST_ENV,
      );
      expect(response.status).toBe(405);
      expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
      expect(response.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
    }

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("no browser-visible response body includes upstream auth secrets", async () => {
    stubUpstreamFetch(JSON.stringify({ status: "ok" }));

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    const bodyText = await response.text();
    expect(bodyText).not.toContain(TEST_ENV.ORIGENLAB_API_AUTH_TOKEN);
    expect(bodyText).not.toContain(TEST_ENV.CF_ACCESS_CLIENT_ID);
    expect(bodyText).not.toContain(TEST_ENV.CF_ACCESS_CLIENT_SECRET);
    expect(response.headers.get(API_AUTH_HEADER)).toBeNull();
    expect(response.headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBeNull();
    expect(response.headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBeNull();
  });

  it("POST is rejected with 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", { method: "POST" }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("unknown upstream path is rejected", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/emails", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("OPTIONS returns 204 without upstream fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "OPTIONS" }),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("upstream 302 is converted to 502 JSON with code upstream_redirect_blocked", async () => {
    const redirectLocation = "https://api.origenlab.cl/cdn-cgi/access/login?redirect_url=%2Fhealth";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response("<html>Cloudflare Access login</html>", {
          status: 302,
          headers: {
            Location: redirectLocation,
            "Content-Type": "text/html",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(502);
    expect(response.headers.get("Content-Type")).toContain("application/json");
    expect(response.headers.get("Cache-Control")).toBe("no-store, private");
    expect(response.headers.get("X-OriginLab-Proxy")).toBe("dashboard-proxy");
    expect(response.headers.get("X-OriginLab-Upstream-Status")).toBe("302");

    const body = (await response.json()) as { error: { code: string } };
    expect(body.error.code).toBe("upstream_redirect_blocked");
  });

  it("upstream redirect response includes Access-Control-Allow-Origin for allowed Origin", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response("", {
          status: 302,
          headers: { Location: "https://api.origenlab.cl/cdn-cgi/access/login" },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(502);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
    expect(response.headers.get("Access-Control-Allow-Credentials")).toBe("true");
  });

  it("upstream redirect response does not include Location header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response("", {
          status: 302,
          headers: { Location: "https://api.origenlab.cl/cdn-cgi/access/login" },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.headers.get("Location")).toBeNull();
  });

  it("upstream redirect response body does not leak secrets or upstream Location", async () => {
    const redirectLocation = "https://api.origenlab.cl/cdn-cgi/access/login?token=secret";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(`redirect to ${redirectLocation}`, {
          status: 302,
          headers: { Location: redirectLocation },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    const bodyText = await response.text();
    expect(bodyText).not.toContain(TEST_ENV.ORIGENLAB_API_AUTH_TOKEN);
    expect(bodyText).not.toContain(TEST_ENV.CF_ACCESS_CLIENT_ID);
    expect(bodyText).not.toContain(TEST_ENV.CF_ACCESS_CLIENT_SECRET);
    expect(bodyText).not.toContain(redirectLocation);
    expect(bodyText).not.toContain("cdn-cgi/access/login");
    expect(response.headers.get(API_AUTH_HEADER)).toBeNull();
    expect(response.headers.get(CF_ACCESS_CLIENT_ID_HEADER)).toBeNull();
    expect(response.headers.get(CF_ACCESS_CLIENT_SECRET_HEADER)).toBeNull();
  });

  it("normal 200 response still includes CORS and proxy diagnostics", async () => {
    stubUpstreamFetch(JSON.stringify({ status: "ok" }));

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("X-OriginLab-Proxy")).toBe("dashboard-proxy");
    expect(response.headers.get("X-OriginLab-Upstream-Status")).toBe("200");
  });
});
