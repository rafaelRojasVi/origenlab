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
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", { method: "GET" }),
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
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", { method: "GET" }),
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

describe("tender attachment navigation: exact GET-only forwarding", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const NAVIGATION_PATH =
    "/api/operator/procurement/tenders/2410-66-LP26/attachment-navigation";

  function stubNavigationUpstream() {
    const captured: { url: string; method: string }[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (req: Request) => {
        captured.push({
          url: req.url,
          method: req.method,
        });

        return new Response(
          JSON.stringify({
            tender_code: "2410-66-LP26",
            destination_kind: "attachments",
            url:
              "https://www.mercadopublico.cl/Procurement/Modules/" +
              "Attachment/ViewAttachmentLC.aspx?enc=EPHEMERAL123",
            ephemeral: true,
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json",
              "Cache-Control": "no-store, private",
              Pragma: "no-cache",
              "X-Request-ID": "upstream-nav-1",
            },
          },
        );
      }),
    );

    return captured;
  }

  it("GET forwards to the exact upstream attachment-navigation endpoint", async () => {
    const captured = stubNavigationUpstream();

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${NAVIGATION_PATH}`, {
        method: "GET",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(captured).toEqual([
      {
        url:
          "https://api.origenlab.cl/operator/procurement/tenders/" +
          "2410-66-LP26/attachment-navigation",
        method: "GET",
      },
    ]);

    const data = (await response.json()) as {
      destination_kind: string;
      ephemeral: boolean;
      url: string;
    };
    expect(data.destination_kind).toBe("attachments");
    expect(data.ephemeral).toBe(true);
    expect(data.url).toContain("ViewAttachmentLC.aspx?enc=EPHEMERAL123");
  });

  it("preserves upstream no-store and no-cache response headers", async () => {
    stubNavigationUpstream();

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${NAVIGATION_PATH}`, {
        method: "GET",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store, private");
    expect(response.headers.get("Pragma")).toBe("no-cache");
    expect(response.headers.get("X-Request-ID")).toBe("upstream-nav-1");
  });

  it("POST to attachment-navigation remains 405 and never reaches upstream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${NAVIGATION_PATH}`, {
        method: "POST",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});


describe("annex-bundle preview upload: exact method+path authorization", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const PREVIEW_PATH = "/api/operator/procurement/tenders/2410-66-LP26/annex-bundle/preview";
  const ZIP_BYTES = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0x01, 0x02, 0x03]);

  function stubUpstreamCapture() {
    const captured: { url: string; method: string; headers: Headers; bodyText: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (req: Request) => {
        const bodyText = await req.clone().text();
        captured.push({ url: req.url, method: req.method, headers: req.headers, bodyText });
        return new Response(JSON.stringify({ result: "imported" }), {
          status: 200,
          headers: { "Content-Type": "application/json", "X-Request-ID": "upstream-req-1" },
        });
      }),
    );
    return captured;
  }

  it("GET on every other allowlisted path remains allowed (unchanged)", async () => {
    stubUpstreamFetch();
    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/procurement/tenders/2410-66-LP26", {
        method: "GET",
      }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
  });

  it("POST to the exact preview path is allowed and forwards the body byte-identically", async () => {
    const captured = stubUpstreamCapture();

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(captured).toHaveLength(1);
    expect(captured[0]?.url).toBe(
      "https://api.origenlab.cl/operator/procurement/tenders/2410-66-LP26/annex-bundle/preview",
    );
    expect(captured[0]?.method).toBe("POST");
    const expectedText = new TextDecoder().decode(ZIP_BYTES);
    expect(captured[0]?.bodyText).toBe(expectedText);
  });

  it("POST to the exact preview path preserves Content-Type upstream", async () => {
    const captured = stubUpstreamCapture();

    await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(captured[0]?.headers.get("Content-Type")).toBe("application/zip");
  });

  it("POST to the exact preview path injects server-side auth without leaking it back", async () => {
    const captured = stubUpstreamCapture();

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(captured[0]?.headers.get(API_AUTH_HEADER)).toBe("server-only-token");
    expect(response.headers.get(API_AUTH_HEADER)).toBeNull();
    const bodyText = await response.text();
    expect(bodyText).not.toContain("server-only-token");
  });

  it("POST to a different exact tender path is still 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/procurement/tenders/2410-66-LP26", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("POST to /operator/procurement/status is still 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/procurement/status", {
        method: "POST",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("POST to a queues path is still 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/procurement/queues/current_opportunity", {
        method: "POST",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("PUT/PATCH/DELETE to the preview path are still 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    for (const method of ["PUT", "PATCH", "DELETE"] as const) {
      const response = await handleRequest(
        requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, { method }),
        TEST_ENV,
      );
      expect(response.status).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("POST to a deeper/invalid upload-shaped path is 405", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(
        "https://dashboard.origenlab.cl/api/operator/procurement/tenders/2410-66-LP26/annex-bundle/preview/extra",
        { method: "POST" },
      ),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adding POST to ALLOWED_METHODS globally would have been wrong: GET-only paths never became POST surfaces", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/procurement/institutions", {
        method: "POST",
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("upstream redirect from a POST preview response is still blocked with 502", async () => {
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
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(502);
  });

  it("no caching header on the preview response, matching every other route", async () => {
    stubUpstreamCapture();

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: ZIP_BYTES,
      }),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-OriginLab-Proxy")).toBe("dashboard-proxy");
  });

  it("OPTIONS preflight for the preview path advertises POST in Access-Control-Allow-Methods", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(`https://dashboard.origenlab.cl${PREVIEW_PATH}`, { method: "OPTIONS" }),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(response.headers.get("Access-Control-Allow-Methods")).toBe("GET, HEAD, OPTIONS, POST");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("OPTIONS preflight for a read-only path still advertises only GET, HEAD, OPTIONS", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin("https://dashboard.origenlab.cl/api/operator/status", { method: "OPTIONS" }),
      TEST_ENV,
    );

    expect(response.headers.get("Access-Control-Allow-Methods")).toBe("GET, HEAD, OPTIONS");
  });
});

describe("commercial operations command forwarding", () => {
  const env = {
    ORIGENLAB_API_UPSTREAM: "https://api.example.com",
    ORIGENLAB_API_AUTH_TOKEN: "secret",
  };

  it("forwards an exact allowed commercial POST", async () => {
    const opportunityId = `o_${"a".repeat(32)}`;

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            opportunity_id: opportunityId,
            confirmation_status: "confirmed",
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json",
            },
          },
        ),
      );

    const request = new Request(
      `https://dashboard.origenlab.cl/api/operations/opportunities/${opportunityId}/state`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Cf-Access-Authenticated-User-Email":
            "tatiana@origenlab.cl",
        },
        body: JSON.stringify({
          confirmation_status: "confirmed",
          expected_version: 0,
        }),
      },
    );

    const { handleRequest } = await import("./index");
    const response = await handleRequest(request, env);

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const upstreamRequest = fetchMock.mock.calls[0][0] as Request;

    expect(upstreamRequest.method).toBe("POST");
    expect(upstreamRequest.url).toContain(
      `/operations/opportunities/${opportunityId}/state`,
    );

    expect(
      upstreamRequest.headers.get("X-OriginLab-Operator-Email"),
    ).toBe("tatiana@origenlab.cl");

    fetchMock.mockRestore();
  });

  it("keeps non-allowlisted commercial mutations at 405", async () => {
    const taskId = `task_${"b".repeat(32)}`;

    const fetchMock = vi.spyOn(globalThis, "fetch");

    const { handleRequest } = await import("./index");

    for (const path of [
      `/api/operations/tasks/${taskId}/delete`,
      `/api/operations/tasks/${taskId}/reopen`,
      "/api/operations/unknown",
    ]) {
      const response = await handleRequest(
        new Request(
          `https://dashboard.origenlab.cl${path}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: "{}",
          },
        ),
        env,
      );

      expect(response.status, path).toBe(405);

      const payload = (await response.json()) as {
        error: {
          code: string;
        };
      };

      expect(
        payload.error.code,
        path,
      ).toBe("method_not_allowed");
    }

    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });

  it("still rejects PUT PATCH and DELETE on allowed CRM paths", async () => {
    const taskId = `task_${"c".repeat(32)}`;

    const fetchMock = vi.spyOn(globalThis, "fetch");
    const { handleRequest } = await import("./index");

    for (const method of ["PUT", "PATCH", "DELETE"]) {
      const response = await handleRequest(
        new Request(
          `https://dashboard.origenlab.cl/api/operations/tasks/${taskId}/complete`,
          {
            method,
          },
        ),
        env,
      );

      expect(response.status, method).toBe(405);
    }

    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });
});


describe("commercial idempotency forwarding", () => {
  it("forwards Idempotency-Key byte-for-byte upstream", () => {
    const incoming = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key":
        "activity:550e8400-e29b-41d4-a716-446655440000",
    });

    const headers = buildUpstreamHeaders(
      TEST_ENV,
      incoming,
    );

    expect(
      headers.get("Idempotency-Key"),
    ).toBe(
      "activity:550e8400-e29b-41d4-a716-446655440000",
    );
  });
});

describe("CRM sales opportunity proxy boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("commercial promotion preflight advertises POST and Idempotency-Key", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(
        "https://dashboard.origenlab.cl/api/operations/sales-opportunities/promote",
        {
          method: "OPTIONS",
          headers: {
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers":
              "content-type,idempotency-key",
          },
        },
      ),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();

    expect(
      response.headers.get("Access-Control-Allow-Methods"),
    ).toBe("GET, HEAD, OPTIONS, POST");

    expect(
      response.headers.get("Access-Control-Allow-Headers"),
    ).toContain("Idempotency-Key");
  });

  it("forwards CRM promotion with trusted operator and idempotency headers", async () => {
    const captured: {
      url: string;
      method: string;
      headers: Headers;
      body: string;
    }[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (req: Request) => {
        captured.push({
          url: req.url,
          method: req.method,
          headers: req.headers,
          body: await req.text(),
        });

        return new Response(
          JSON.stringify({
            sales_opportunity_id:
              `sales_${"d".repeat(32)}`,
            source_kind: "pr3",
            source_opportunity_id:
              `o_${"a".repeat(32)}`,
            account_id: "a_1",
            primary_contact_id: "c_1",
            title: "Centrifuga",
            stage: "new",
            owner_key: "tatiana@origenlab.cl",
            created_by: "tatiana@origenlab.cl",
            created_at: "2026-08-26T00:00:00Z",
          }),
          {
            status: 201,
            headers: {
              "Content-Type": "application/json",
            },
          },
        );
      }),
    );

    const body = JSON.stringify({
      source_opportunity_id:
        `o_${"a".repeat(32)}`,
      title: "Centrifuga",
      owner_key: "tatiana@origenlab.cl",
    });

    const response = await handleRequest(
      requestWithOrigin(
        "https://dashboard.origenlab.cl/api/operations/sales-opportunities/promote",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": "crm-promote-1",
            "Cf-Access-Authenticated-User-Email":
              "Tatiana@OrigenLab.CL",
          },
          body,
        },
      ),
      TEST_ENV,
    );

    expect(response.status).toBe(201);
    expect(captured).toHaveLength(1);

    expect(captured[0]?.url).toBe(
      "https://api.origenlab.cl/operations/sales-opportunities/promote",
    );
    expect(captured[0]?.method).toBe("POST");
    expect(
      captured[0]?.headers.get("Idempotency-Key"),
    ).toBe("crm-promote-1");
    expect(
      captured[0]?.headers.get(
        "X-OriginLab-Operator-Email",
      ),
    ).toBe("tatiana@origenlab.cl");
    expect(captured[0]?.body).toBe(body);
  });
});


describe("CRM-2 sales-opportunity lifecycle proxy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const salesOpportunityId = `sales_${"d".repeat(32)}`;
  const lifecyclePath =
    `/api/operations/sales-opportunities/${salesOpportunityId}/stage`;

  it("forwards the exact lifecycle POST body and trusted operator identity", async () => {
    const captured: {
      url: string;
      method: string;
      headers: Headers;
      bodyText: string;
    }[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (req: Request) => {
        captured.push({
          url: req.url,
          method: req.method,
          headers: req.headers,
          bodyText: await req.clone().text(),
        });

        return new Response(
          JSON.stringify({
            sales_opportunity_id: salesOpportunityId,
            source_kind: "pr3",
            source_opportunity_id: `o_${"a".repeat(32)}`,
            account_id: "account_1",
            primary_contact_id: "contact_1",
            title: "Centrífuga refrigerada",
            stage: "qualifying",
            owner_key: "tatiana@origenlab.cl",
            version: 2,
            created_by: "tatiana@origenlab.cl",
            updated_by: "tatiana@origenlab.cl",
            created_at: "2026-08-26T15:00:00Z",
            updated_at: "2026-08-26T16:00:00Z",
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json",
              "X-Request-ID": "crm2-stage-1",
            },
          },
        );
      }),
    );

    const body = JSON.stringify({
      stage: "qualifying",
      expected_version: 1,
    });

    const response = await handleRequest(
      requestWithOrigin(
        `https://dashboard.origenlab.cl${lifecyclePath}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Cf-Access-Authenticated-User-Email":
              "Tatiana@OrigenLab.CL",
            "X-OriginLab-Operator-Email":
              "spoofed@attacker.example",
          },
          body,
        },
      ),
      TEST_ENV,
    );

    expect(response.status).toBe(200);
    expect(captured).toHaveLength(1);

    expect(captured[0]?.url).toBe(
      `https://api.origenlab.cl/operations/` +
        `sales-opportunities/${salesOpportunityId}/stage`,
    );

    expect(captured[0]?.method).toBe("POST");
    expect(captured[0]?.bodyText).toBe(body);

    expect(
      captured[0]?.headers.get(
        "X-OriginLab-Operator-Email",
      ),
    ).toBe("tatiana@origenlab.cl");

    expect(
      captured[0]?.headers.get(
        "X-OriginLab-Operator-Email",
      ),
    ).not.toBe("spoofed@attacker.example");

    expect(
      captured[0]?.headers.get(API_AUTH_HEADER),
    ).toBe("server-only-token");

    // CRM-2 lifecycle mutation uses expected_version rather than
    // create-command idempotency.
    expect(
      captured[0]?.headers.get("Idempotency-Key"),
    ).toBeNull();

    expect(
      response.headers.get("X-Request-ID"),
    ).toBe("crm2-stage-1");
  });

  it("advertises POST for exact lifecycle preflight without fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await handleRequest(
      requestWithOrigin(
        `https://dashboard.origenlab.cl${lifecyclePath}`,
        {
          method: "OPTIONS",
        },
      ),
      TEST_ENV,
    );

    expect(response.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();

    expect(
      response.headers.get("Access-Control-Allow-Methods"),
    ).toBe("GET, HEAD, OPTIONS, POST");

    // Idempotency-Key remains an allowed browser header for the
    // commercial-command family; the CRM-2 stage endpoint does not
    // require or synthesize it.
    expect(
      response.headers.get("Access-Control-Allow-Headers"),
    ).toContain("Idempotency-Key");
  });

  it("rejects broadened lifecycle POST paths before upstream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const rejected = [
      `/api/operations/sales-opportunities/${salesOpportunityId}/stage/extra`,
      `/api/operations/sales-opportunities/${salesOpportunityId}/delete`,
      "/api/operations/sales-opportunities/sales_short/stage",
    ];

    for (const path of rejected) {
      const response = await handleRequest(
        requestWithOrigin(
          `https://dashboard.origenlab.cl${path}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              stage: "qualifying",
              expected_version: 1,
            }),
          },
        ),
        TEST_ENV,
      );

      expect(response.status, path).toBe(405);
    }

    expect(fetchMock).not.toHaveBeenCalled();
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
    "/v2/commands/resolve-unsubscribe-review",
    "/v2/commands/dismiss-unsubscribe-review",
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
    "/v2/workspace/overview",
    "/v2/workspace/pipeline",
    "/v2/workspace/drive",
    "/v2/workspace/review",
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
