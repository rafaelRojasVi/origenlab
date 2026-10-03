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
  const fetchMock = vi.fn(async () => {
    return new Response(body, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": "upstream-req-1",
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
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

  it("GET /api/health forwards upstream with X-OriginLab-API-Key", async () => {
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
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", {
        method: "GET",
        headers: { Accept: "application/json", "X-Request-ID": "browser-req-1" },
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(captured).toHaveLength(1);
    expect(captured[0]?.url).toBe("https://api.origenlab.cl/health");
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

  it("refuses a V1 read with 403 and never calls upstream", async () => {
    const fetchMock = stubUpstreamFetch();
    const res = await handleRequest(requestWithOrigin("https://proxy.test/api/operator/status"), TEST_ENV);
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ error: { code: "path_not_allowed" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a V1 command POST with 405 and never calls upstream", async () => {
    const fetchMock = stubUpstreamFetch();
    const res = await handleRequest(
      requestWithOrigin("https://proxy.test/api/operations/sales-opportunities/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "x-1" },
        body: "{}",
      }),
      TEST_ENV,
    );
    expect(res.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
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

  it("OPTIONS /api/health returns 204 and CORS headers", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", {
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

  it("upstream CORS headers cannot override dashboard CORS policy for allowed Origin", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: {
            "Access-Control-Allow-Origin": "https://evil.example.com",
            "Access-Control-Allow-Credentials": "false",
            "Access-Control-Allow-Methods": "GET, POST, DELETE",
            "Access-Control-Allow-Headers": "Authorization, X-OriginLab-API-Key",
            "Access-Control-Expose-Headers": "Server-Timing, X-OriginLab-API-Key",
            "Access-Control-Max-Age": "86400",
            "Content-Type": "application/json",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("Access-Control-Allow-Credentials")).toBe("true");
    expect(response.headers.get("Access-Control-Allow-Methods")).toBe("GET, HEAD, OPTIONS");
    expect(response.headers.get("Access-Control-Allow-Headers")).toBe(
      "Accept, Content-Type, X-Request-ID",
    );
    expect(response.headers.get("Access-Control-Expose-Headers")).toBe("X-Request-ID");
    expect(response.headers.get("Access-Control-Max-Age")).toBeNull();
  });

  it("upstream CORS headers cannot grant access to disallowed Origin", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, DELETE",
            "Access-Control-Allow-Headers": "Authorization, X-OriginLab-API-Key",
            "Access-Control-Expose-Headers": "Server-Timing, X-OriginLab-API-Key",
            "Access-Control-Max-Age": "86400",
            "Content-Type": "application/json",
          },
        });
      }),
    );

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
    expect(response.headers.get("Access-Control-Allow-Methods")).toBeNull();
    expect(response.headers.get("Access-Control-Allow-Headers")).toBeNull();
    expect(response.headers.get("Access-Control-Expose-Headers")).toBeNull();
    expect(response.headers.get("Access-Control-Max-Age")).toBeNull();
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
        requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method }),
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
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "POST" }),
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

  it.each([
    "/api/contacts/a%40b.co",
    "/api/contacts/anything?limit=5",
    "/api/mirror/commercial/deals",
    "/api/mirror/leads/prospects?limit=20",
    "/api/mirror/catalog/products",
  ])("V1 path %s answers 403 path_not_allowed and never reaches upstream", async (path) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${path}`, {
        method: "GET",
        headers: { Cookie: "origenlab_session=abc" },
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: { code: "path_not_allowed" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("V1 paths stay refused for POST as well", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    for (const path of ["/api/contacts/a%40b.co", "/api/mirror/commercial/deals"]) {
      const response = await handleRequest(
        requestWithOrigin(`https://dashboard.origenlab.cl${path}`, { method: "POST" }),
        TEST_ENV,
      );
      expect(response.status).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("missing upstream configuration returns JSON error without fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      { ...TEST_ENV, ORIGENLAB_API_UPSTREAM: "" },
    );

    expect(response.status).toBe(500);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(response.headers.get("Content-Type")).toContain("application/json");
    expect(response.headers.get("Cache-Control")).toBe("no-store, private");
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);

    const body = (await response.json()) as { error: { code: string } };
    expect(body.error.code).toBe("upstream_not_configured");
  });

  it("missing API auth token returns JSON error without fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      { ...TEST_ENV, ORIGENLAB_API_AUTH_TOKEN: " " },
    );

    expect(response.status).toBe(500);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(response.headers.get("Content-Type")).toContain("application/json");
    expect(response.headers.get("Cache-Control")).toBe("no-store, private");
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);

    const body = (await response.json()) as { error: { code: string } };
    expect(body.error.code).toBe("auth_token_not_configured");
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

  it("normal upstream 200 with Set-Cookie does not forward Set-Cookie", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Set-Cookie": "CF_Authorization=upstream-api-token; Path=/; HttpOnly; Secure",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Set-Cookie")).toBeNull();
    expect(response.headers.get("Set-Cookie2")).toBeNull();
    expect(response.headers.getSetCookie?.() ?? []).toEqual([]);
  });

  it("upstream 200 with Set-Cookie still includes Access-Control-Allow-Origin for allowed Origin", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Set-Cookie": "CF_Authorization=upstream-api-token; Path=/; HttpOnly; Secure",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(PRODUCTION_ORIGIN);
    expect(response.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
    expect(response.headers.get("Access-Control-Allow-Credentials")).toBe("true");
  });

  it("upstream 200 with Set-Cookie still includes proxy diagnostic headers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Set-Cookie": "CF_Authorization=upstream-api-token; Path=/; HttpOnly; Secure",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.headers.get("X-OriginLab-Proxy")).toBe("dashboard-proxy");
    expect(response.headers.get("X-OriginLab-Upstream-Status")).toBe("200");
  });

  it("upstream redirect blocked response does not include Location or Set-Cookie", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response("", {
          status: 302,
          headers: {
            Location: "https://api.origenlab.cl/cdn-cgi/access/login",
            "Set-Cookie": "CF_Authorization=upstream-api-token; Path=/; HttpOnly; Secure",
          },
        });
      }),
    );

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/health", { method: "GET" }),
      TEST_ENV,
    );

    expect(response.status).toBe(502);
    expect(response.headers.get("Location")).toBeNull();
    expect(response.headers.get("Set-Cookie")).toBeNull();
    expect(response.headers.get("Set-Cookie2")).toBeNull();
    expect(response.headers.get("Access-Control-Allow-Origin")).not.toBe("*");
  });
});

describe("Marketing commands (draft create/save, audience freeze)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const FREEZE = "https://proxy.test/api/v2/commands/freeze-campaign-audience";
  const good = (extra: Record<string, string> = {}, body = JSON.stringify({ confirmed: true })) =>
    requestWithOrigin(FREEZE, {
      method: "POST",
      body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "9b1c7d2e-4f3a-4c1b-8e2d-1a2b3c4d5e6f",
        "Sec-Fetch-Site": "same-site",
        Cookie: "__Host-origenlab_session=s1; CF_Authorization=leak",
        ...extra,
      },
    });

  it("forwards a same-site JSON command with its key and only the session cookie", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(good(), TEST_ENV);
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe("/v2/commands/freeze-campaign-audience");
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Idempotency-Key")).toBe("9b1c7d2e-4f3a-4c1b-8e2d-1a2b3c4d5e6f");
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
    expect(await upstream.text()).toBe(JSON.stringify({ confirmed: true }));
  });

  it.each([
    ["no Origin", good({}), { origin: "" }, 403, "origin_not_allowed"],
    ["a foreign Origin", good({}), { origin: "https://evil.test" }, 403, "origin_not_allowed"],
    ["a cross-site fetch", good({ "Sec-Fetch-Site": "cross-site" }), {}, 403, "cross_site_request"],
    ["a form post", good({ "Content-Type": "application/x-www-form-urlencoded" }), {}, 415, "unsupported_media_type"],
    ["text/plain", good({ "Content-Type": "text/plain" }), {}, 415, "unsupported_media_type"],
    ["no key", good({ "Idempotency-Key": "" }), {}, 400, "idempotency_key_required"],
    ["a malformed key", good({ "Idempotency-Key": "short" }), {}, 400, "idempotency_key_required"],
    ["a declared oversize body", good({ "Content-Length": "9999999" }), {}, 413, "payload_too_large"],
  ])("refuses %s before anything is forwarded", async (_label, base, over, status, code) => {
    stubUpstreamFetch();
    const headers = new Headers(base.headers);
    if ((over as { origin?: string }).origin !== undefined) {
      const origin = (over as { origin: string }).origin;
      if (origin) headers.set("Origin", origin);
      else headers.delete("Origin");
    }
    const req = new Request(base.url, { method: "POST", headers, body: JSON.stringify({ confirmed: true }) });
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ error: { code } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("refuses an actual body larger than the limit even when Content-Length understates it", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(good({}, "x".repeat(3_500_001)), TEST_ENV);
    expect(res.status).toBe(413);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("advertises POST and Idempotency-Key on the preflight for allowed origins only", async () => {
    const pre = await handleRequest(requestWithOrigin(FREEZE, { method: "OPTIONS" }), TEST_ENV);
    expect(pre.headers.get("Access-Control-Allow-Methods")).toContain("POST");
    expect(pre.headers.get("Access-Control-Allow-Headers")).toContain("Idempotency-Key");
    const foreign = await handleRequest(requestWithOrigin(FREEZE, { method: "OPTIONS", origin: "https://evil.test" }), TEST_ENV);
    expect(foreign.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });

  it("does not let the Marketing reads accept a write", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(
      requestWithOrigin("https://proxy.test/api/v2/workspace/marketing", {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "9b1c7d2e-4f3a-4c1b" },
      }),
      TEST_ENV,
    );
    expect(res.status).toBe(405);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

describe("Campaign planning command and the sent-HTML archive read", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const PLAN = "https://proxy.test/api/v2/commands/set-campaign-planning";
  const BODY = JSON.stringify({
    campaign_id: "96301691-af05-41ea-82e3-05f5fae40837",
    expected_planning_version: 0,
    planned_for_date: "2027-03-14",
    planned_for_time: "09:30",
  });
  const plan = (extra: Record<string, string> = {}, body = BODY, method = "POST") =>
    requestWithOrigin(PLAN, {
      method,
      body: method === "GET" || method === "HEAD" ? undefined : body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "plan-9b1c7d2e-4f3a-4c1b-8e2d",
        "Sec-Fetch-Site": "same-origin",
        Cookie: "__Host-origenlab_session=s1; CF_Authorization=leak",
        ...extra,
      },
    });

  it("forwards a same-origin JSON planning command with its key and only the session cookie", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(plan(), TEST_ENV);
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe("/v2/commands/set-campaign-planning");
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Idempotency-Key")).toBe("plan-9b1c7d2e-4f3a-4c1b-8e2d");
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
    expect(await upstream.text()).toBe(BODY);
  });

  it.each([
    [
      "a foreign Origin",
      requestWithOrigin(PLAN, {
        method: "POST",
        body: BODY,
        origin: "https://evil.test",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "plan-9b1c7d2e-4f3a-4c1b-8e2d" },
      }),
      403,
      "origin_not_allowed",
    ],
    [
      "no Origin",
      requestWithOrigin(PLAN, {
        method: "POST",
        body: BODY,
        origin: "",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "plan-9b1c7d2e-4f3a-4c1b-8e2d" },
      }),
      403,
      "origin_not_allowed",
    ],
    ["a cross-site fetch", plan({ "Sec-Fetch-Site": "cross-site" }), 403, "cross_site_request"],
    ["a form post", plan({ "Content-Type": "application/x-www-form-urlencoded" }), 415, "unsupported_media_type"],
    ["no key", plan({ "Idempotency-Key": "" }), 400, "idempotency_key_required"],
    ["a declared body over 4 kB", plan({ "Content-Length": "4097" }), 413, "payload_too_large"],
  ])("refuses %s before anything is forwarded", async (_label, req, status, code) => {
    stubUpstreamFetch();
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ error: { code } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("refuses a planning body over 4 kB even when Content-Length understates it", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(plan({}, JSON.stringify({ pad: "x".repeat(5_000) })), TEST_ENV);
    expect(res.status).toBe(413);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it.each(["PATCH", "PUT", "DELETE"])("refuses %s on the planning path", async (method) => {
    stubUpstreamFetch();
    const res = await handleRequest(plan({}, BODY, method), TEST_ENV);
    expect(res.status).toBe(405);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("does not make the planning command GET-readable", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(plan({}, BODY, "GET"), TEST_ENV);
    expect(res.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("keeps the 3.5 MB limit for the draft commands", async () => {
    stubUpstreamFetch();
    const req = requestWithOrigin("https://proxy.test/api/v2/commands/save-campaign-draft", {
      method: "POST",
      body: JSON.stringify({ body_html: "x".repeat(10_000) }),
      headers: { "Content-Type": "application/json", "Idempotency-Key": "draft-9b1c7d2e-4f3a" },
    });
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(200);
  });

  it("forwards the archive read as GET and refuses a write to it", async () => {
    stubUpstreamFetch();
    const archive = "https://proxy.test/api/v2/workspace/marketing/campaigns/96301691-af05-41ea-82e3-05f5fae40837/archive";
    const read = await handleRequest(requestWithOrigin(archive, { method: "GET" }), TEST_ENV);
    expect(read.status).toBe(200);
    const write = await handleRequest(
      requestWithOrigin(archive, {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "archive-9b1c7d2e" },
      }),
      TEST_ENV,
    );
    expect(write.status).toBe(405);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
});

describe("W10 suppression status", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the suppression read as GET with the masked body untouched, and refuses a write to it", async () => {
    const masked = JSON.stringify({ entries: [{ address: "***@uni.invalid", reason: "unsubscribe" }] });
    stubUpstreamFetch(masked);
    const path = "https://proxy.test/api/v2/workspace/marketing/suppressions";
    const read = await handleRequest(requestWithOrigin(path, { method: "GET" }), TEST_ENV);
    expect(read.status).toBe(200);
    expect(await read.text()).toBe(masked);
    const write = await handleRequest(
      requestWithOrigin(path, {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "suppression-read-probe" },
      }),
      TEST_ENV,
    );
    expect(write.status).toBe(405);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it.each([
    "/v2/commands/apply-unsubscribe-replies",
    "/v2/unsubscribe/preview",
  ])(
    "never forwards the unsubscribe tooling %s, whatever the method",
    async (path) => {
      stubUpstreamFetch();
      for (const method of ["GET", "POST"]) {
        const res = await handleRequest(
          requestWithOrigin(`https://proxy.test/api${path}`, {
            method,
            ...(method === "POST"
              ? { body: JSON.stringify({ records: [], expected_input_sha256: "0".repeat(64) }),
                  headers: { "Content-Type": "application/json", "Idempotency-Key": "unsubscribe-tooling-probe" } }
              : {}),
          }),
          TEST_ENV,
        );
        expect([403, 405]).toContain(res.status);
      }
      expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    },
  );
});

describe("W10 review decisions (confirm / dismiss a held «BAJA»)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const BODY = JSON.stringify({
    assertion_id: "96301691-af05-41ea-82e3-05f5fae40837",
    expected_address: "remitente@lab.invalid",
    expected_review_sha256: "c".repeat(64),
    explanation: "Respondió BAJA a otro boletín",
  });
  const review = (path: string, extra: Record<string, string> = {}, body = BODY, method = "POST") =>
    requestWithOrigin(`https://proxy.test/api${path}`, {
      method,
      body: method === "GET" || method === "HEAD" ? undefined : body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "review-9b1c7d2e-4f3a-4c1b-8e2d",
        "Sec-Fetch-Site": "same-origin",
        Cookie: "__Host-origenlab_session=s1; CF_Authorization=leak",
        ...extra,
      },
    });
  const PATHS = ["/v2/commands/resolve-unsubscribe-review", "/v2/commands/dismiss-unsubscribe-review"];

  it.each(PATHS)("forwards a same-origin JSON %s with its key and only the session cookie", async (path) => {
    stubUpstreamFetch();
    const res = await handleRequest(review(path), TEST_ENV);
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe(path);
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Idempotency-Key")).toBe("review-9b1c7d2e-4f3a-4c1b-8e2d");
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
    expect(await upstream.text()).toBe(BODY);
  });

  it.each(PATHS.flatMap((path) => [
    [path, "a foreign Origin", { Origin: "https://evil.test" }, 403, "origin_not_allowed"],
    [path, "a cross-site fetch", { "Sec-Fetch-Site": "cross-site" }, 403, "cross_site_request"],
    [path, "a form post", { "Content-Type": "application/x-www-form-urlencoded" }, 415, "unsupported_media_type"],
    [path, "no key", { "Idempotency-Key": "" }, 400, "idempotency_key_required"],
    [path, "a declared body over 8 kB", { "Content-Length": "8193" }, 413, "payload_too_large"],
  ] as const))("%s: refuses %s before anything is forwarded", async (path, _label, extra, status, code) => {
    stubUpstreamFetch();
    const req = "Origin" in extra
      ? requestWithOrigin(`https://proxy.test/api${path}`, {
          method: "POST", body: BODY, origin: extra.Origin,
          headers: { "Content-Type": "application/json", "Idempotency-Key": "review-9b1c7d2e-4f3a-4c1b-8e2d" },
        })
      : review(path, extra as Record<string, string>);
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ error: { code } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it.each(PATHS)("refuses a %s body over 8 kB even when Content-Length understates it", async (path) => {
    stubUpstreamFetch();
    const res = await handleRequest(review(path, {}, JSON.stringify({ pad: "x".repeat(9_000) })), TEST_ENV);
    expect(res.status).toBe(413);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it.each(PATHS)("does not make %s GET-readable", async (path) => {
    stubUpstreamFetch();
    const res = await handleRequest(review(path, {}, BODY, "GET"), TEST_ENV);
    expect(res.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

describe("CRM card reads (supplier directory, observed equipment interests)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const READS = ["/v2/workspace/providers", "/v2/workspace/equipment-interests"];

  it.each(READS)("forwards GET %s with only the session cookie and no forged operator", async (path) => {
    // What a viewer receives upstream: the address masked, an opaque ref to join by.
    const masked = JSON.stringify({ persons: [{ address: "***@uni.invalid", address_ref: "0123456789abcdef01234567" }] });
    stubUpstreamFetch(masked);
    const res = await handleRequest(
      requestWithOrigin(`https://proxy.test/api${path}`, {
        headers: {
          Cookie: "CF_Authorization=edge; __Host-origenlab_session=s1; other=1",
          "X-OriginLab-Operator-Email": "admin@origenlab.cl",
        },
      }),
      TEST_ENV,
    );
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe(path);
    expect(upstream.method).toBe("GET");
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
    // The browser cannot name the operator (and so cannot pick an unredacted role).
    expect(upstream.headers.get("X-OriginLab-Operator-Email")).toBeNull();
    // The upstream answer, masked for a viewer, is passed through untouched.
    expect(await res.text()).toBe(masked);
  });

  it.each(READS)("replaces a forged operator header with the Cloudflare Access identity on %s", async (path) => {
    stubUpstreamFetch();
    await handleRequest(
      requestWithOrigin(`https://proxy.test/api${path}`, {
        headers: {
          "X-OriginLab-Operator-Email": "admin@origenlab.cl",
          "Cf-Access-Authenticated-User-Email": "Viewer@OrigenLab.cl",
        },
      }),
      TEST_ENV,
    );
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(upstream.headers.get("X-OriginLab-Operator-Email")).toBe("viewer@origenlab.cl");
  });

  it.each(
    READS.flatMap((path) => ["POST", "PUT", "PATCH", "DELETE"].map((method) => [method, path] as const)),
  )("refuses %s %s with 405 before anything is forwarded", async (method, path) => {
    stubUpstreamFetch();
    const res = await handleRequest(
      requestWithOrigin(`https://proxy.test/api${path}`, {
        method,
        body: "{}",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": "9b1c7d2e-4f3a-4c1b-8e2d-1a2b3c4d5e6f",
          Cookie: "__Host-origenlab_session=s1",
        },
      }),
      TEST_ENV,
    );
    expect(res.status).toBe(405);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it.each([
    "/v2/workspace/providers/directory",
    "/v2/workspace/equipment-interests/persons",
    "/v2/workspace/equipment-interest",
    "/v2/workspace/providers/",
  ])("refuses the neighbouring GET %s with 403", async (path) => {
    stubUpstreamFetch();
    const res = await handleRequest(
      requestWithOrigin(`https://proxy.test/api${path}`, { headers: { Cookie: "__Host-origenlab_session=s1" } }),
      TEST_ENV,
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: { code: "path_not_allowed" } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

describe("Campaign safety block commands and read", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const BLOCK = "https://proxy.test/api/v2/commands/block-campaign";
  const UNBLOCK = "https://proxy.test/api/v2/commands/unblock-campaign";
  const BODY = JSON.stringify({
    scope: "campaign",
    campaign_id: "96301691-af05-41ea-82e3-05f5fae40837",
    expected_block_version: 0,
    reason: "Incidente en revisión",
  });
  const post = (url: string, extra: Record<string, string> = {}, body = BODY, method = "POST") =>
    requestWithOrigin(url, {
      method,
      body: method === "GET" || method === "HEAD" ? undefined : body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "block-command-key-0001",
        "Sec-Fetch-Site": "same-origin",
        Cookie: "__Host-origenlab_session=s1; CF_Authorization=leak",
        ...extra,
      },
    });

  it.each([BLOCK, UNBLOCK])("forwards a same-origin JSON command to %s with its key and only the session cookie", async (url) => {
    stubUpstreamFetch();
    const res = await handleRequest(post(url), TEST_ENV);
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe(new URL(url).pathname.replace(/^\/api/, ""));
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Idempotency-Key")).toBe("block-command-key-0001");
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
  });

  it.each([
    [
      "a foreign Origin",
      requestWithOrigin(BLOCK, {
        method: "POST",
        body: BODY,
        origin: "https://evil.test",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "block-command-key-0001" },
      }),
      403,
      "origin_not_allowed",
    ],
    ["a cross-site fetch", post(UNBLOCK, { "Sec-Fetch-Site": "cross-site" }), 403, "cross_site_request"],
    ["a form post", post(BLOCK, { "Content-Type": "application/x-www-form-urlencoded" }), 415, "unsupported_media_type"],
    ["no key", post(BLOCK, { "Idempotency-Key": "" }), 400, "idempotency_key_required"],
    ["a declared body over 16 kB", post(UNBLOCK, { "Content-Length": "16385" }), 413, "payload_too_large"],
  ])("refuses %s before anything is forwarded", async (_label, req, status, code) => {
    stubUpstreamFetch();
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ error: { code } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("refuses a block body over 16 kB even when Content-Length understates it", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(post(BLOCK, {}, JSON.stringify({ reason: "x".repeat(20_000) })), TEST_ENV);
    expect(res.status).toBe(413);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("does not make the block commands GET-readable", async () => {
    stubUpstreamFetch();
    for (const url of [BLOCK, UNBLOCK]) {
      const res = await handleRequest(post(url, {}, BODY, "GET"), TEST_ENV);
      expect(res.status).toBe(403);
    }
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("forwards the block read as GET and refuses a write to it", async () => {
    stubUpstreamFetch(JSON.stringify({ all_campaigns: { blocked: false }, legacy: [] }));
    const path = "https://proxy.test/api/v2/workspace/marketing/campaign-blocks";
    const read = await handleRequest(requestWithOrigin(path, { method: "GET" }), TEST_ENV);
    expect(read.status).toBe(200);
    const write = await handleRequest(
      requestWithOrigin(path, {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "blocks-read-probe" },
      }),
      TEST_ENV,
    );
    expect(write.status).toBe(405);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
});

describe("CRM authoring commands", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const CREATE_PERSON = "https://proxy.test/api/v2/commands/create-person";
  const KEY = "test-idempotency-key-crm-authoring";

  function good(extra: Record<string, string> = {}, body = JSON.stringify({ display_name: "Paloma" })) {
    return requestWithOrigin(CREATE_PERSON, {
      method: "POST",
      body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": KEY,
        "Sec-Fetch-Site": "same-site",
        Cookie: "__Host-origenlab_session=s1; CF_Authorization=leak",
        ...extra,
      },
    });
  }

  it("forwards a well-formed same-site JSON authoring command with its key and session cookie only", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(good(), TEST_ENV);
    expect(res.status).toBe(200);
    const upstream = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe("/v2/commands/create-person");
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("Idempotency-Key")).toBe(KEY);
    expect(upstream.headers.get("Cookie")).toBe("__Host-origenlab_session=s1");
  });

  it.each([
    ["no Origin", {}, { origin: "" }, 403, "origin_not_allowed"],
    ["a foreign Origin", {}, { origin: "https://evil.test" }, 403, "origin_not_allowed"],
    ["a cross-site fetch", { "Sec-Fetch-Site": "cross-site" }, {}, 403, "cross_site_request"],
    ["text/plain Content-Type", { "Content-Type": "text/plain" }, {}, 415, "unsupported_media_type"],
    ["no Idempotency-Key", { "Idempotency-Key": "" }, {}, 400, "idempotency_key_required"],
    ["malformed Idempotency-Key", { "Idempotency-Key": "short" }, {}, 400, "idempotency_key_required"],
  ])("refuses %s before forwarding", async (_label, headerOver, originOver, status, code) => {
    stubUpstreamFetch();
    const base = good(headerOver as Record<string, string>);
    const headers = new Headers(base.headers);
    if ((originOver as { origin?: string }).origin !== undefined) {
      const origin = (originOver as { origin: string }).origin;
      if (origin) headers.set("Origin", origin);
      else headers.delete("Origin");
    }
    const req = new Request(base.url, { method: "POST", headers, body: JSON.stringify({ display_name: "P" }) });
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(status);
    expect(await res.json()).toMatchObject({ error: { code } });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("refuses an actual body larger than 64 KiB even when Content-Length understates it", async () => {
    stubUpstreamFetch();
    const res = await handleRequest(good({}, "x".repeat(65_537)), TEST_ENV);
    expect(res.status).toBe(413);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("does not make a CRM authoring command path GET-readable", async () => {
    stubUpstreamFetch();
    const req = requestWithOrigin(CREATE_PERSON, { method: "GET" });
    const res = await handleRequest(req, TEST_ENV);
    expect(res.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
