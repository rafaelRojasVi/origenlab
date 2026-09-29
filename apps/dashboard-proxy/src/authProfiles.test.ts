/**
 * The profile screen behind a shared Workspace sign-in, through the Worker.
 *
 * Exactly three new upstream routes -- GET /auth/profiles, POST /auth/profile/select and POST
 * /auth/profile/clear -- carry the session cookie both ways, and every sign-in POST (logout
 * included) must pass the Origin / Sec-Fetch-Site guard. Nothing else under /auth/profile*,
 * and never the API's local-only /auth/dev/* shortcut, is reachable.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTH_SESSION_COOKIE, forwardsSessionCookie } from "./auth";
import { AUTH_PROFILE_MAX_BYTES, isAllowedAuthPostPath, isAllowedUpstreamPath } from "./allowlist";
import { authCommandRefusal, handleRequest } from "./index";
import { OPERATOR_EMAIL_HEADER, type ProxyEnv } from "./proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
};
const DASHBOARD = "https://dashboard.origenlab.cl";
const SELECT = `${DASHBOARD}/api/auth/profile/select`;
const CLEAR = `${DASHBOARD}/api/auth/profile/clear`;
const BODY = JSON.stringify({ profile_id: "00000000-0000-4000-8000-000000000001", pin: "482913" });

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

function post(url: string, headers: Record<string, string>, body: string | null = BODY): Request {
  return new Request(url, { method: "POST", headers, body });
}

const GOOD = {
  Origin: DASHBOARD,
  "Sec-Fetch-Site": "same-origin",
  "Content-Type": "application/json",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("profile routes allowlist", () => {
  it("lists exactly the three profile routes and not the dev shortcut", () => {
    expect(isAllowedUpstreamPath("/auth/profiles")).toBe(true);
    expect(isAllowedAuthPostPath("/auth/profile/select")).toBe(true);
    expect(isAllowedAuthPostPath("/auth/profile/clear")).toBe(true);
    expect(isAllowedAuthPostPath("/auth/logout")).toBe(true);
    for (const path of [
      "/auth/profile",
      "/auth/profile/",
      "/auth/profile/select/",
      "/auth/profile/select/x",
      "/auth/profile/delete",
      "/auth/profiles",
      "/auth/dev/principal-session",
    ]) {
      expect(isAllowedAuthPostPath(path), path).toBe(false);
    }
    for (const path of ["/auth/profile/select", "/auth/profile/clear", "/auth/dev/principal-session",
      "/auth/profiles/x", "/auth/profiles/"]) {
      expect(isAllowedUpstreamPath(path), path).toBe(false);
    }
  });

  it("forwards the session cookie to the three routes", () => {
    for (const path of ["/auth/profiles", "/auth/profile/select", "/auth/profile/clear"]) {
      expect(forwardsSessionCookie(path), path).toBe(true);
    }
    expect(forwardsSessionCookie("/auth/dev/principal-session")).toBe(false);
  });

  it.each([
    ["GET", "/api/auth/profile/select", 403],
    ["GET", "/api/auth/profile/clear", 403],
    ["POST", "/api/auth/profiles", 405],
    ["PUT", "/api/auth/profile/select", 405],
    ["DELETE", "/api/auth/profile/clear", 405],
    ["PATCH", "/api/auth/profiles", 405],
    ["POST", "/api/auth/dev/principal-session", 405],
    ["GET", "/api/auth/dev/principal-session", 403],
    ["POST", "/api/auth/profile/other", 405],
  ])("refuses %s %s before the upstream", async (method, path, status) => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    const response = await handleRequest(
      new Request(`${DASHBOARD}${path}`, { method, headers: GOOD, body: method === "GET" ? null : BODY }),
      TEST_ENV,
    );
    expect(response.status).toBe(status);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("profile selection through the Worker", () => {
  it("forwards a same-origin JSON selection with only the session cookie, and passes its cookie back", async () => {
    const fetchMock = stubUpstream({
      status: 200,
      body: '{"authenticated":true}',
      headers: [
        ["Set-Cookie", `${AUTH_SESSION_COOKIE}=new.tag; Path=/; Secure; HttpOnly; SameSite=lax`],
        ["Set-Cookie", "tracker=1"],
      ],
    });
    const response = await handleRequest(
      post(SELECT, {
        ...GOOD,
        Cookie: `CF_Authorization=cf; ${AUTH_SESSION_COOKIE}=old.tag; other=1`,
        [OPERATOR_EMAIL_HEADER]: "admin@attacker.example",
        "X-OriginLab-Operator-Id": "00000000-0000-4000-8000-0000000000ad",
        "X-OriginLab-Profile-Id": "00000000-0000-4000-8000-0000000000ad",
      }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
    expect(response.headers.getSetCookie()).toEqual([
      `${AUTH_SESSION_COOKIE}=new.tag; Path=/; Secure; HttpOnly; SameSite=lax`,
    ]);
    const upstream = fetchMock.mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe("/auth/profile/select");
    expect(upstream.headers.get("Cookie")).toBe(`${AUTH_SESSION_COOKIE}=old.tag`);
    // No browser-supplied operator or profile identity ever reaches the API.
    expect(upstream.headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
    expect(upstream.headers.get("X-OriginLab-Operator-Id")).toBeNull();
    expect(upstream.headers.get("X-OriginLab-Profile-Id")).toBeNull();
    expect(await upstream.text()).toBe(BODY);
  });

  it("forwards GET /auth/profiles with the session cookie", async () => {
    const fetchMock = stubUpstream({ status: 200, body: '{"profiles":[]}' });
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/auth/profiles`, {
        headers: { Origin: DASHBOARD, Cookie: `${AUTH_SESSION_COOKIE}=s.t` },
      }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
    expect((fetchMock.mock.calls[0][0] as Request).headers.get("Cookie")).toBe(`${AUTH_SESSION_COOKIE}=s.t`);
  });

  it.each([
    [SELECT, { ...GOOD, Origin: "https://evil.example" }, 403, "origin_not_allowed"],
    [SELECT, { "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" }, 403, "origin_not_allowed"],
    [SELECT, { ...GOOD, "Sec-Fetch-Site": "cross-site" }, 403, "cross_site_request"],
    [SELECT, { ...GOOD, "Content-Type": "application/x-www-form-urlencoded" }, 415, "unsupported_media_type"],
    [SELECT, { ...GOOD, "Content-Type": "text/plain" }, 415, "unsupported_media_type"],
    [CLEAR, { ...GOOD, Origin: "null" }, 403, "origin_not_allowed"],
    [CLEAR, { ...GOOD, "Content-Type": "multipart/form-data; boundary=x" }, 415, "unsupported_media_type"],
    [`${DASHBOARD}/api/auth/logout`, { "Sec-Fetch-Site": "same-origin" }, 403, "origin_not_allowed"],
    [`${DASHBOARD}/api/auth/logout`, { Origin: DASHBOARD, "Sec-Fetch-Site": "cross-site" }, 403, "cross_site_request"],
  ])("refuses a cross-site or non-JSON sign-in POST (%s)", async (url, headers, status, code) => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    const response = await handleRequest(post(url, headers as Record<string, string>), TEST_ENV);
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: { code } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an oversized selection body, declared or not", async () => {
    const fetchMock = stubUpstream({ status: 200, body: "{}" });
    const big = "x".repeat(AUTH_PROFILE_MAX_BYTES + 1);
    const declared = await handleRequest(
      post(SELECT, { ...GOOD, "Content-Length": String(big.length) }, big),
      TEST_ENV,
    );
    expect(declared.status).toBe(413);
    const undeclared = await handleRequest(post(SELECT, GOOD, big), TEST_ENV);
    expect(undeclared.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("the guard itself accepts only the dashboard origin", () => {
    expect(authCommandRefusal(post(SELECT, GOOD))).toBeNull();
    expect(authCommandRefusal(post(SELECT, { ...GOOD, Origin: "http://localhost:5173" }))).toBeNull();
    expect(authCommandRefusal(post(SELECT, { ...GOOD, Origin: "https://dashboard.origenlab.cl.evil.example" })))
      .toEqual({ status: 403, code: "origin_not_allowed" });
  });

  it("never lets an upstream redirect through from a profile route", async () => {
    stubUpstream({ status: 302, headers: [["Location", `${DASHBOARD}/`]] });
    const response = await handleRequest(post(SELECT, GOOD), TEST_ENV);
    expect(response.status).toBe(502);
  });
});

describe("sign-in answers carry no timing", () => {
  const TIMED: [string, string][] = [
    ["Server-Timing", "app;dur=412.07"],
    ["X-Process-Time-Ms", "412.07"],
    ["Content-Type", "application/json"],
  ];

  for (const [name, request] of [
    ["profile select (refused)", () => post(SELECT, GOOD)],
    ["profile clear", () => post(CLEAR, GOOD, "{}")],
    ["logout", () => post(`${DASHBOARD}/api/auth/logout`, { Origin: DASHBOARD }, null)],
    ["session", () => new Request(`${DASHBOARD}/api/auth/session`)],
    ["profiles", () => new Request(`${DASHBOARD}/api/auth/profiles`)],
  ] as [string, () => Request][]) {
    it(`drops upstream timing headers on ${name}`, async () => {
      stubUpstream({ status: name.includes("refused") ? 401 : 200, headers: TIMED, body: "{}" });
      const response = await handleRequest(request(), TEST_ENV);
      expect(response.headers.get("Server-Timing"), name).toBeNull();
      expect(response.headers.get("X-Process-Time-Ms"), name).toBeNull();
      expect(response.headers.get("Content-Type"), name).toBe("application/json");
    });
  }

  it("leaves them on a non-sign-in route", async () => {
    stubUpstream({ status: 200, headers: TIMED, body: "{}" });
    const response = await handleRequest(new Request(`${DASHBOARD}/api/health`), TEST_ENV);
    expect(response.headers.get("Server-Timing")).toBe("app;dur=412.07");
  });
});
