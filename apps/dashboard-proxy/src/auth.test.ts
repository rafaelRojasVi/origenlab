import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_SESSION_COOKIE,
  AUTH_SIGNIN_COOKIE,
  filterAuthCookieHeader,
  forwardsSessionCookie,
  isAllowedAuthRedirect,
} from "./auth";
import { handleRequest } from "./index";
import { OPERATOR_EMAIL_HEADER, type ProxyEnv } from "./proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
};
const DASHBOARD = "https://dashboard.origenlab.cl";
const GOOGLE_LOGIN =
  "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&scope=openid+email+profile";

function stubUpstream(init: { status: number; headers?: [string, string][]; body?: string }) {
  const fetchMock = vi.fn(async (_req: Request) => {
    const headers = new Headers();
    for (const [name, value] of init.headers ?? []) {
      headers.append(name, value);
    }
    return new Response(init.body ?? null, { status: init.status, headers });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function upstreamRequest(fetchMock: ReturnType<typeof stubUpstream>): Request {
  return fetchMock.mock.calls[0][0] as Request;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sign-in cookie filtering", () => {
  it("keeps only the two OrigenLab sign-in cookies", () => {
    expect(
      filterAuthCookieHeader(
        `CF_Authorization=abc; ${AUTH_SESSION_COOKIE}=s.t; other=1; ${AUTH_SIGNIN_COOKIE}=x.y`,
      ),
    ).toBe(`${AUTH_SESSION_COOKIE}=s.t; ${AUTH_SIGNIN_COOKIE}=x.y`);
    expect(filterAuthCookieHeader("CF_Authorization=abc; origenlab_session=dev")).toBeNull();
    expect(filterAuthCookieHeader(null)).toBeNull();
  });

  it("forwards the session cookie only to /auth and /v2", () => {
    expect(forwardsSessionCookie("/auth/session")).toBe(true);
    expect(forwardsSessionCookie("/v2/contacts")).toBe(true);
    expect(forwardsSessionCookie("/operator/status")).toBe(false);
    expect(forwardsSessionCookie("/operations/tasks")).toBe(false);
    expect(forwardsSessionCookie("/v2")).toBe(false);
  });

  it("sends the session cookie upstream on a /v2 read and drops every other cookie", async () => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    await handleRequest(
      new Request(`${DASHBOARD}/api/v2/contacts`, {
        headers: { Cookie: `CF_Authorization=edge; ${AUTH_SESSION_COOKIE}=signed.value` },
      }),
      TEST_ENV,
    );
    expect(upstreamRequest(fetchMock).headers.get("Cookie")).toBe(
      `${AUTH_SESSION_COOKIE}=signed.value`,
    );
  });

  it("sends no cookie at all to a V1 path", async () => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    await handleRequest(
      new Request(`${DASHBOARD}/api/operator/status`, {
        headers: { Cookie: `${AUTH_SESSION_COOKIE}=signed.value` },
      }),
      TEST_ENV,
    );
    expect(upstreamRequest(fetchMock).headers.get("Cookie")).toBeNull();
  });

  it("still never forwards a browser-supplied operator header on an auth path", async () => {
    const fetchMock = stubUpstream({ status: 401, body: "{}" });
    await handleRequest(
      new Request(`${DASHBOARD}/api/auth/session`, {
        headers: { [OPERATOR_EMAIL_HEADER]: "spoofed@origenlab.cl" },
      }),
      TEST_ENV,
    );
    expect(upstreamRequest(fetchMock).headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });
});

describe("sign-in redirects", () => {
  it("lets the login route redirect to Google's authorization endpoint only", () => {
    expect(isAllowedAuthRedirect("/auth/google/login", GOOGLE_LOGIN, DASHBOARD)).toBe(true);
    expect(
      isAllowedAuthRedirect("/auth/google/login", "https://evil.example/o/oauth2/v2/auth", DASHBOARD),
    ).toBe(false);
    expect(
      isAllowedAuthRedirect("/auth/google/login", "https://accounts.google.com/logout", DASHBOARD),
    ).toBe(false);
    expect(isAllowedAuthRedirect("/auth/google/login", null, DASHBOARD)).toBe(false);
    expect(isAllowedAuthRedirect("/auth/google/login", "not a url", DASHBOARD)).toBe(false);
  });

  it("lets the callback redirect only to the dashboard root, with at most a login_error code", () => {
    const ok = (loc: string) => isAllowedAuthRedirect("/auth/google/callback", loc, DASHBOARD);
    expect(ok(`${DASHBOARD}/`)).toBe(true);
    expect(ok(`${DASHBOARD}/?login_error=unknown_operator`)).toBe(true);
    expect(ok("https://evil.example/")).toBe(false);
    expect(ok(`${DASHBOARD}/somewhere`)).toBe(false);
    expect(ok(`${DASHBOARD}/?next=https://evil.example`)).toBe(false);
    expect(ok(`${DASHBOARD}/?login_error=<script>`)).toBe(false);
    expect(ok(`${DASHBOARD}/#/x`)).toBe(false);
    expect(ok("http://dashboard.origenlab.cl/")).toBe(false);
  });

  it("allows no redirect from any other path", () => {
    expect(isAllowedAuthRedirect("/auth/session", `${DASHBOARD}/`, DASHBOARD)).toBe(false);
    expect(isAllowedAuthRedirect("/v2/contacts", GOOGLE_LOGIN, DASHBOARD)).toBe(false);
  });

  it("passes the login redirect and only the sign-in cookie through", async () => {
    stubUpstream({
      status: 302,
      headers: [
        ["Location", GOOGLE_LOGIN],
        ["Set-Cookie", `${AUTH_SIGNIN_COOKIE}=tx.sig; HttpOnly; Path=/; SameSite=lax; Secure`],
        ["Set-Cookie", "tracking=1; Path=/"],
      ],
    });
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/auth/google/login`),
      TEST_ENV,
    );
    expect(response.status).toBe(302);
    expect(response.headers.get("Location")).toBe(GOOGLE_LOGIN);
    expect(response.headers.getSetCookie()).toEqual([
      `${AUTH_SIGNIN_COOKIE}=tx.sig; HttpOnly; Path=/; SameSite=lax; Secure`,
    ]);
    expect(response.headers.get("Cache-Control")).toBe("no-store, private");
  });

  it("passes the callback redirect back to the dashboard with the session cookie", async () => {
    stubUpstream({
      status: 303,
      headers: [
        ["Location", `${DASHBOARD}/`],
        ["Set-Cookie", `${AUTH_SIGNIN_COOKIE}=""; Max-Age=0; Path=/; Secure`],
        ["Set-Cookie", `${AUTH_SESSION_COOKIE}=s.sig; HttpOnly; Max-Age=28800; Path=/; Secure`],
      ],
    });
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/auth/google/callback?code=c&state=s`),
      TEST_ENV,
    );
    expect(response.status).toBe(303);
    expect(response.headers.getSetCookie()).toHaveLength(2);
  });

  it("still blocks a callback redirect that leaves the dashboard", async () => {
    stubUpstream({ status: 303, headers: [["Location", "https://evil.example/"]] });
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/auth/google/callback?code=c&state=s`),
      TEST_ENV,
    );
    expect(response.status).toBe(502);
    expect(response.headers.get("Location")).toBeNull();
  });

  it("still blocks every non-auth redirect", async () => {
    stubUpstream({ status: 302, headers: [["Location", GOOGLE_LOGIN]] });
    const response = await handleRequest(new Request(`${DASHBOARD}/api/v2/contacts`), TEST_ENV);
    expect(response.status).toBe(502);
  });
});

describe("sign-in route allowlist", () => {
  it("allows POST /auth/logout and passes back only its sign-in cookies", async () => {
    stubUpstream({
      status: 200,
      body: '{"authenticated":false}',
      headers: [
        ["Set-Cookie", `${AUTH_SESSION_COOKIE}=""; Max-Age=0; Path=/; Secure`],
        ["Set-Cookie", "other=1"],
      ],
    });
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/auth/logout`, { method: "POST" }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
    expect(response.headers.getSetCookie()).toEqual([
      `${AUTH_SESSION_COOKIE}=""; Max-Age=0; Path=/; Secure`,
    ]);
  });

  it("strips Set-Cookie from every non-auth path, as before", async () => {
    stubUpstream({
      status: 200,
      body: "{}",
      headers: [["Set-Cookie", `${AUTH_SESSION_COOKIE}=forged; Path=/`]],
    });
    const response = await handleRequest(new Request(`${DASHBOARD}/api/v2/contacts`), TEST_ENV);
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it.each([
    ["GET", "/api/auth/logout", 403],
    ["POST", "/api/auth/session", 405],
    ["POST", "/api/auth/google/login", 405],
    ["POST", "/api/auth/google/callback", 405],
    ["GET", "/api/auth/google", 403],
    ["GET", "/api/auth/google/other", 403],
    ["DELETE", "/api/auth/logout", 405],
  ])("refuses %s %s", async (method, path, status) => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    const response = await handleRequest(new Request(`${DASHBOARD}${path}`, { method }), TEST_ENV);
    expect(response.status).toBe(status);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
