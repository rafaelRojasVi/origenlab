import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTH_SESSION_COOKIE } from "./auth";
import { handleRequest } from "./index";
import { OPERATOR_EMAIL_HEADER, type ProxyEnv } from "./proxy";

/**
 * The eight CRM workspace reads through the Worker. The Worker's part is narrow and pinned
 * here: exact paths, GET only, the dashboard session cookie and nothing else goes upstream,
 * no browser-supplied identity, and the API's answer — 401 without a session, a masked body
 * for a viewer — comes back unchanged. Authentication and the viewer mask themselves are
 * enforced by apps/api and pinned in its suite (`test_v2_crm_workspace.py`,
 * `test_v2_contact_redaction.py`).
 */

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
};
const DASHBOARD = "https://dashboard.origenlab.cl";
const READS = [
  "/v2/workspace/overview",
  "/v2/workspace/pipeline",
  "/v2/workspace/providers",
  "/v2/workspace/marketing",
  "/v2/workspace/drive",
  "/v2/workspace/review",
  "/v2/workspace/person-suggestions",
  "/v2/workspace/mail-sync",
  "/v2/cockpit/work-queue",
];

function stubUpstream(status: number, body = "{}", headers: [string, string][] = []) {
  const fetchMock = vi.fn(async (_req: Request) => new Response(body, { status, headers }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CRM workspace reads", () => {
  it.each(READS)("forwards GET %s with only the session cookie", async (path) => {
    const fetchMock = stubUpstream(200);
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api${path}`, {
        headers: {
          Cookie: `CF_Authorization=edge; ${AUTH_SESSION_COOKIE}=signed.value; other=1`,
          [OPERATOR_EMAIL_HEADER]: "spoofed@origenlab.cl",
        },
      }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const upstream = fetchMock.mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe(path);
    expect(upstream.method).toBe("GET");
    expect(upstream.headers.get("Cookie")).toBe(`${AUTH_SESSION_COOKIE}=signed.value`);
    expect(upstream.headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });

  it.each(READS)("forwards an unauthenticated GET %s with no cookie at all", async (path) => {
    // The Worker does not decide who is signed in; the API answers 401 (pinned in apps/api
    // `test_v2_proxied_workspace_reads.py`). What the Worker must not do is invent a session.
    const fetchMock = stubUpstream(401, '{"detail":"sign in"}');
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api${path}`, { headers: { Cookie: "CF_Authorization=edge; other=1" } }),
      TEST_ENV,
    );
    expect(response.status).toBe(401);
    const upstream = fetchMock.mock.calls[0][0] as Request;
    expect(upstream.headers.get("Cookie")).toBeNull();
    expect(upstream.headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });

  it("forwards no operator header even when a Cloudflare Access header arrives", async () => {
    const fetchMock = stubUpstream(200);
    await handleRequest(
      new Request(`${DASHBOARD}/api/v2/workspace/overview`, {
        headers: {
          "Cf-Access-Authenticated-User-Email": " Person@OrigenLab.cl ",
          [OPERATOR_EMAIL_HEADER]: "spoofed@origenlab.cl",
        },
      }),
      TEST_ENV,
    );
    const upstream = fetchMock.mock.calls[0][0] as Request;
    expect(upstream.headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });

  it("keeps the work-queue query string", async () => {
    const fetchMock = stubUpstream(200);
    await handleRequest(new Request(`${DASHBOARD}/api/v2/cockpit/work-queue?limit=200`), TEST_ENV);
    expect(new URL((fetchMock.mock.calls[0][0] as Request).url).search).toBe("?limit=200");
  });

  it.each(READS.flatMap((p) => ["POST", "PUT", "PATCH", "DELETE"].map((m) => [m, p])))(
    "refuses %s %s without forwarding it",
    async (method, path) => {
      const fetchMock = stubUpstream(200);
      const response = await handleRequest(new Request(`${DASHBOARD}/api${path}`, { method }), TEST_ENV);
      expect(response.status).toBe(405);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("passes the API's 401 back when there is no session, with no cookie set", async () => {
    stubUpstream(401, '{"detail":"sign in"}', [["Set-Cookie", `${AUTH_SESSION_COOKIE}=forged; Path=/`]]);
    const response = await handleRequest(new Request(`${DASHBOARD}/api/v2/workspace/pipeline`), TEST_ENV);
    expect(response.status).toBe(401);
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it("passes a viewer's masked answer back unchanged", async () => {
    const masked = '{"items":[{"contact":{"address":"***@example.cl"}}]}';
    stubUpstream(200, masked, [["X-OrigenLab-Redaction", "contact-addresses"]]);
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/v2/workspace/pipeline`, { headers: { Cookie: `${AUTH_SESSION_COOKIE}=v` } }),
      TEST_ENV,
    );
    expect(await response.text()).toBe(masked);
  });

  it("refuses a neighbouring path without forwarding it", async () => {
    const fetchMock = stubUpstream(200);
    for (const path of ["/v2/workspace/pipeline/x", "/v2/cockpit/kpis", "/v2/cockpit/case-archive"]) {
      const response = await handleRequest(new Request(`${DASHBOARD}/api${path}`), TEST_ENV);
      expect(response.status, path).toBe(403);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("CRM authoring reads", () => {
  const uuid = "96301691-af05-51ea-82e3-05f5fae40837";
  const AUTHORING_READS = [
    `/v2/workspace/people/${uuid}`,
    "/v2/workspace/people/merge-preview",
    `/v2/workspace/organizations/${uuid}/authoring`,
  ];

  it.each(AUTHORING_READS)("forwards GET %s with the session cookie only", async (path) => {
    const fetchMock = stubUpstream(200, JSON.stringify({ person_id: uuid }));
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api${path}`, {
        headers: {
          Cookie: `CF_Authorization=edge; ${AUTH_SESSION_COOKIE}=session.token; other=1`,
          [OPERATOR_EMAIL_HEADER]: "spoofed@origenlab.cl",
        },
      }),
      TEST_ENV,
    );
    expect(response.status).toBe(200);
    const upstream = fetchMock.mock.calls[0][0] as Request;
    expect(new URL(upstream.url).pathname).toBe(path);
    expect(upstream.method).toBe("GET");
    expect(upstream.headers.get("Cookie")).toBe(`${AUTH_SESSION_COOKIE}=session.token`);
    expect(upstream.headers.get(OPERATOR_EMAIL_HEADER)).toBeNull();
  });

  it("passes a viewer's masked answer back unchanged", async () => {
    const masked = JSON.stringify({ person_id: uuid, contact_points: [{ address: "***@example.cl" }] });
    stubUpstream(200, masked, [["X-OrigenLab-Redaction", "contact-addresses"]]);
    const response = await handleRequest(
      new Request(`${DASHBOARD}/api/v2/workspace/people/${uuid}`, {
        headers: { Cookie: `${AUTH_SESSION_COOKIE}=v` },
      }),
      TEST_ENV,
    );
    expect(await response.text()).toBe(masked);
  });

  it.each(AUTHORING_READS.flatMap((p) => ["POST", "PUT", "PATCH", "DELETE"].map((m) => [m, p])))(
    "refuses %s %s without forwarding it",
    async (method, path) => {
      const fetchMock = stubUpstream(200);
      const response = await handleRequest(new Request(`${DASHBOARD}/api${path}`, { method }), TEST_ENV);
      expect(response.status).toBe(405);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("refuses the bare people list and per-person notes (not yet a browser surface)", async () => {
    const fetchMock = stubUpstream(200);
    for (const path of [
      "/v2/workspace/people",
      `/v2/workspace/people/${uuid}/notes`,
    ]) {
      const response = await handleRequest(new Request(`${DASHBOARD}/api${path}`), TEST_ENV);
      expect(response.status, path).toBe(403);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
