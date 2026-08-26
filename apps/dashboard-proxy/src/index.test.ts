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
