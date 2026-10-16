/**
 * The Cotizador through the Worker: five JSON commands (Idempotency-Key required) and the live
 * preview, a POST that is a read (JSON, Origin and Sec-Fetch-Site checked, no Idempotency-Key).
 * Every value here is synthetic.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { CATALOG_COMMAND_MAX_BYTES } from "./allowlist";
import { handleRequest } from "./index";
import type { ProxyEnv } from "./proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
};
const DASHBOARD = "https://dashboard.origenlab.cl";
const PREVIEW = `${DASHBOARD}/api/v2/quotes/preview`;
const SAVE = `${DASHBOARD}/api/v2/commands/save-quote-draft`;
const COMMANDS = [
  "create-quote",
  "save-quote-draft",
  "approve-quote-revision",
  "void-quote-revision",
  "create-quote-revision",
].map((name) => `${DASHBOARD}/api/v2/commands/${name}`);
const JSON_TYPE = "application/json";
const COMMAND_HEADERS = {
  Origin: DASHBOARD,
  "Sec-Fetch-Site": "same-origin",
  "Content-Type": JSON_TYPE,
  "Idempotency-Key": "quote-key-0001",
};
const PREVIEW_HEADERS = {
  Origin: DASHBOARD,
  "Sec-Fetch-Site": "same-origin",
  "Content-Type": JSON_TYPE,
};

function stubUpstream() {
  const fetchMock = vi.fn(async (_req: Request) => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function post(url: string, headers: Record<string, string>, body: string | ArrayBuffer = "{}"): Request {
  return new Request(url, { method: "POST", headers, body });
}

async function code(res: Response): Promise<string> {
  return ((await res.json()) as { error: { code: string } }).error.code;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A realistic, fully populated sixty-line draft: the largest ordinary body this boundary sees. */
function syntheticDraft(lines: number): string {
  const id = "96301691-af05-51ea-82e3-05f5fae40837";
  const items = Array.from({ length: lines }, (_, i) => ({
    product_id: id,
    description: `Equipo de laboratorio sintético número ${i}, `.padEnd(120, "descripción de prueba "),
    quantity: "12.500000",
    item_type: "equipment",
    unit_cost: "1234.560000",
    cost_currency: "USD",
    supplier_organization_id: id,
    cost_observation_id: id,
    margin_mode: "markup",
    margin_pct: "0.350000",
    typed_unit_price: "1999.990000",
    is_principal: false,
    line_note_es: `Nota de línea sintética ${i}: `.padEnd(200, "condiciones de entrega y garantía de prueba "),
  }));
  return JSON.stringify({
    revision_id: id,
    expected_version: 3,
    draft: {
      items,
      manual_expenses: Array.from({ length: 20 }, (_, i) => ({ label: `Gasto sintético ${i}`, amount: "1000.000000", currency: "CLP" })),
      expense_method: "profile",
      allocation_mode: "principal",
      principal_rule: "highest_cost",
      fx_overrides: [{ currency: "USD", clp_per_unit: "950.12345678", reason: "Tipo de cambio de prueba" }],
      terms: {
        delivery_terms: "x".repeat(300),
        validity_days: 30,
        payment_terms: "y".repeat(300),
        warranty_terms_es: "z".repeat(300),
        delivery_place_es: "w".repeat(300),
        installation_terms_es: "v".repeat(300),
        client_notes_es: "u".repeat(300),
        terms_extra: Array.from({ length: 12 }, (_, i) => ({ label_es: `Término ${i}`, text_es: "t".repeat(300) })),
      },
      recipient_participant_id: id,
    },
  });
}

describe("quote body size", () => {
  it("a realistic sixty-line save-quote-draft fits the 64 KiB command limit", () => {
    const bytes = new TextEncoder().encode(syntheticDraft(60)).byteLength;
    console.info(`60-line synthetic save-quote-draft body: ${bytes} bytes (limit ${CATALOG_COMMAND_MAX_BYTES})`);
    expect(bytes).toBeLessThanOrEqual(CATALOG_COMMAND_MAX_BYTES);
  });
});

describe("quote commands", () => {
  it("forwards each JSON command with the same guards as the catalog commands", async () => {
    for (const url of COMMANDS) {
      const fetchMock = stubUpstream();
      const res = await handleRequest(post(url, COMMAND_HEADERS), TEST_ENV);
      expect(res.status, url).toBe(200);
      expect(fetchMock, url).toHaveBeenCalledTimes(1);
    }
  });

  it("refuses a command without an Idempotency-Key, with a foreign Origin, cross-site, or non-JSON", async () => {
    for (const url of COMMANDS) {
      const fetchMock = stubUpstream();
      const { "Idempotency-Key": _k, ...noKey } = COMMAND_HEADERS;
      expect(await code(await handleRequest(post(url, noKey), TEST_ENV)), url).toBe("idempotency_key_required");
      expect(
        await code(await handleRequest(post(url, { ...COMMAND_HEADERS, Origin: "https://evil.example" }), TEST_ENV)),
      ).toBe("origin_not_allowed");
      expect(
        await code(await handleRequest(post(url, { ...COMMAND_HEADERS, "Sec-Fetch-Site": "cross-site" }), TEST_ENV)),
      ).toBe("cross_site_request");
      expect(
        await code(await handleRequest(post(url, { ...COMMAND_HEADERS, "Content-Type": "text/plain" }), TEST_ENV)),
      ).toBe("unsupported_media_type");
      expect(fetchMock).not.toHaveBeenCalled();
    }
  });

  it("forwards the measured sixty-line draft and refuses a body over the limit", async () => {
    const fetchMock = stubUpstream();
    const body = syntheticDraft(60);
    const ok = await handleRequest(
      post(SAVE, { ...COMMAND_HEADERS, "Content-Length": String(new TextEncoder().encode(body).byteLength) }, body),
      TEST_ENV,
    );
    expect(ok.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const big = "x".repeat(CATALOG_COMMAND_MAX_BYTES + 1);
    const declared = await handleRequest(
      post(SAVE, { ...COMMAND_HEADERS, "Content-Length": String(big.length) }, big),
      TEST_ENV,
    );
    expect(declared.status).toBe(413);
    // An undeclared oversize body is caught on the buffered length.
    const undeclared = await handleRequest(post(SAVE, COMMAND_HEADERS, big), TEST_ENV);
    expect(undeclared.status).toBe(413);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("never reaches a command by GET", async () => {
    const fetchMock = stubUpstream();
    for (const url of COMMANDS) {
      const res = await handleRequest(new Request(url, { method: "GET" }), TEST_ENV);
      expect(res.status, url).toBe(403);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("quote preview", () => {
  it("forwards a JSON POST without an Idempotency-Key", async () => {
    const fetchMock = stubUpstream();
    const res = await handleRequest(post(PREVIEW, PREVIEW_HEADERS, syntheticDraft(60)), TEST_ENV);
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const sent = fetchMock.mock.calls[0][0] as Request;
    expect(sent.method).toBe("POST");
    expect(new URL(sent.url).pathname).toBe("/v2/quotes/preview");
  });

  it("refuses a foreign or missing Origin, cross-site fetches, and non-JSON bodies", async () => {
    const fetchMock = stubUpstream();
    const { Origin: _o, ...noOrigin } = PREVIEW_HEADERS;
    expect(await code(await handleRequest(post(PREVIEW, noOrigin), TEST_ENV))).toBe("origin_not_allowed");
    expect(
      await code(await handleRequest(post(PREVIEW, { ...PREVIEW_HEADERS, Origin: "https://evil.example" }), TEST_ENV)),
    ).toBe("origin_not_allowed");
    expect(
      await code(await handleRequest(post(PREVIEW, { ...PREVIEW_HEADERS, "Sec-Fetch-Site": "cross-site" }), TEST_ENV)),
    ).toBe("cross_site_request");
    for (const type of ["text/plain", "multipart/form-data; boundary=----x", "application/x-www-form-urlencoded"]) {
      expect(
        await code(await handleRequest(post(PREVIEW, { ...PREVIEW_HEADERS, "Content-Type": type }), TEST_ENV)),
        type,
      ).toBe("unsupported_media_type");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a body over the limit, declared or actual", async () => {
    const fetchMock = stubUpstream();
    const big = "x".repeat(CATALOG_COMMAND_MAX_BYTES + 1);
    const declared = await handleRequest(
      post(PREVIEW, { ...PREVIEW_HEADERS, "Content-Length": String(big.length) }, big),
      TEST_ENV,
    );
    expect(declared.status).toBe(413);
    const undeclared = await handleRequest(post(PREVIEW, PREVIEW_HEADERS, big), TEST_ENV);
    expect(undeclared.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is not reachable by GET, HEAD or PUT, and its neighbours stay refused", async () => {
    const fetchMock = stubUpstream();
    expect((await handleRequest(new Request(PREVIEW, { method: "GET" }), TEST_ENV)).status).toBe(403);
    expect((await handleRequest(new Request(PREVIEW, { method: "HEAD" }), TEST_ENV)).status).toBe(403);
    expect(
      (await handleRequest(new Request(PREVIEW, { method: "PUT", headers: PREVIEW_HEADERS, body: "{}" }), TEST_ENV)).status,
    ).toBe(405);
    for (const path of ["/api/v2/quotes/preview/", "/api/v2/quotes/preview/x", "/api/v2/quotes/numbering"]) {
      const res = await handleRequest(post(`${DASHBOARD}${path}`, PREVIEW_HEADERS), TEST_ENV);
      expect(res.status, path).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("advertises POST and the JSON content type on its preflight, without the Idempotency-Key", async () => {
    const res = await handleRequest(
      new Request(PREVIEW, { method: "OPTIONS", headers: { Origin: DASHBOARD } }),
      TEST_ENV,
    );
    expect(res.headers.get("Access-Control-Allow-Methods")).toContain("POST");
    expect(res.headers.get("Access-Control-Allow-Headers")).toContain("Content-Type");
  });
});
