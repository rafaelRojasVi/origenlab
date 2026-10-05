/**
 * Catalog 1a through the Worker: nine JSON commands (64 KiB) and one multipart image upload
 * (8 MiB file plus 64 KiB of form overhead). The upload is the only multipart POST; JSON and
 * multipart never swap places.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { CATALOG_COMMAND_MAX_BYTES, CATALOG_UPLOAD_MAX_BYTES } from "./allowlist";
import { handleRequest } from "./index";
import type { ProxyEnv } from "./proxy";

const TEST_ENV: ProxyEnv = {
  ORIGENLAB_API_UPSTREAM: "https://api.origenlab.cl",
  ORIGENLAB_API_AUTH_TOKEN: "server-only-token",
};
const DASHBOARD = "https://dashboard.origenlab.cl";
const UPLOAD = `${DASHBOARD}/api/v2/commands/add-product-image`;
const CREATE = `${DASHBOARD}/api/v2/commands/create-product`;
const GOOD = {
  Origin: DASHBOARD,
  "Sec-Fetch-Site": "same-origin",
  "Idempotency-Key": "catalog-key-0001",
};
const MULTIPART = "multipart/form-data; boundary=----x";

function stubUpstream() {
  const fetchMock = vi.fn(async (_req: Request) => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function post(url: string, headers: Record<string, string>, body: string | ArrayBuffer = "{}"): Request {
  return new Request(url, { method: "POST", headers, body });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("catalog upload guard", () => {
  it("limits are the contract values", () => {
    expect(CATALOG_COMMAND_MAX_BYTES).toBe(65_536);
    expect(CATALOG_UPLOAD_MAX_BYTES).toBe(8_388_608 + 65_536);
  });

  it("forwards a multipart upload with its Content-Type byte-identical", async () => {
    const fetchMock = stubUpstream();
    const res = await handleRequest(
      post(UPLOAD, { ...GOOD, "Content-Type": MULTIPART }, "--x\r\n"),
      TEST_ENV,
    );
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const sent = fetchMock.mock.calls[0][0] as Request;
    expect(sent.headers.get("Content-Type")).toBe(MULTIPART);
  });

  it("refuses JSON sent to the upload path with 415", async () => {
    const fetchMock = stubUpstream();
    const res = await handleRequest(post(UPLOAD, { ...GOOD, "Content-Type": "application/json" }), TEST_ENV);
    expect(res.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses multipart without a boundary, or with a lookalike type, with 415", async () => {
    const fetchMock = stubUpstream();
    for (const ct of ["multipart/form-data", "multipart/form-data; boundary=", "multipart/mixed; boundary=x", "text/plain"]) {
      const res = await handleRequest(post(UPLOAD, { ...GOOD, "Content-Type": ct }), TEST_ENV);
      expect(res.status, ct).toBe(415);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses multipart sent to a JSON catalog command with 415", async () => {
    const fetchMock = stubUpstream();
    const res = await handleRequest(post(CREATE, { ...GOOD, "Content-Type": MULTIPART }), TEST_ENV);
    expect(res.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a missing or malformed Idempotency-Key with 400", async () => {
    const fetchMock = stubUpstream();
    for (const key of [undefined, "short"]) {
      const headers: Record<string, string> = { ...GOOD, "Content-Type": MULTIPART };
      if (key === undefined) delete headers["Idempotency-Key"];
      else headers["Idempotency-Key"] = key;
      const res = await handleRequest(post(UPLOAD, headers), TEST_ENV);
      expect(res.status).toBe(400);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a foreign Origin and a cross-site request with 403", async () => {
    const fetchMock = stubUpstream();
    const foreign = await handleRequest(
      post(UPLOAD, { ...GOOD, Origin: "https://evil.example", "Content-Type": MULTIPART }),
      TEST_ENV,
    );
    expect(foreign.status).toBe(403);
    const cross = await handleRequest(
      post(UPLOAD, { ...GOOD, "Sec-Fetch-Site": "cross-site", "Content-Type": MULTIPART }),
      TEST_ENV,
    );
    expect(cross.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an upload over 8 MiB + 64 KiB with 413, declared or actual", async () => {
    const fetchMock = stubUpstream();
    const big = new ArrayBuffer(CATALOG_UPLOAD_MAX_BYTES + 1);
    const declared = await handleRequest(
      post(UPLOAD, { ...GOOD, "Content-Type": MULTIPART, "Content-Length": String(big.byteLength) }, big),
      TEST_ENV,
    );
    expect(declared.status).toBe(413);
    const lying = await handleRequest(
      post(UPLOAD, { ...GOOD, "Content-Type": MULTIPART, "Content-Length": "10" }, big),
      TEST_ENV,
    );
    expect(lying.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts an upload of exactly the limit", async () => {
    const fetchMock = stubUpstream();
    const exact = new ArrayBuffer(CATALOG_UPLOAD_MAX_BYTES);
    const res = await handleRequest(post(UPLOAD, { ...GOOD, "Content-Type": MULTIPART }, exact), TEST_ENV);
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("catalog JSON commands guard", () => {
  const COMMANDS = [
    "create-product", "update-product", "confirm-product-content", "record-supplier-cost",
    "set-supplier-terms", "set-cost-parameter", "record-fx-rate", "update-product-image",
    "review-document-line",
  ];

  it("forwards each JSON command", async () => {
    const fetchMock = stubUpstream();
    for (const name of COMMANDS) {
      const res = await handleRequest(
        post(`${DASHBOARD}/api/v2/commands/${name}`, { ...GOOD, "Content-Type": "application/json" }),
        TEST_ENV,
      );
      expect(res.status, name).toBe(200);
    }
    expect(fetchMock).toHaveBeenCalledTimes(COMMANDS.length);
  });

  it("applies the 64 KiB limit, not the marketing 3.5 MB or the upload limit", async () => {
    const fetchMock = stubUpstream();
    const big = "x".repeat(CATALOG_COMMAND_MAX_BYTES + 1);
    const declared = await handleRequest(
      post(CREATE, { ...GOOD, "Content-Type": "application/json", "Content-Length": String(big.length) }, big),
      TEST_ENV,
    );
    expect(declared.status).toBe(413);
    const lying = await handleRequest(
      post(CREATE, { ...GOOD, "Content-Type": "application/json", "Content-Length": "10" }, big),
      TEST_ENV,
    );
    expect(lying.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a missing Idempotency-Key, a foreign Origin and a non-JSON type", async () => {
    const fetchMock = stubUpstream();
    const noKey = { Origin: DASHBOARD, "Content-Type": "application/json" };
    expect((await handleRequest(post(CREATE, noKey), TEST_ENV)).status).toBe(400);
    expect(
      (await handleRequest(post(CREATE, { ...GOOD, Origin: "https://evil.example", "Content-Type": "application/json" }), TEST_ENV)).status,
    ).toBe(403);
    expect((await handleRequest(post(CREATE, { ...GOOD, "Content-Type": "text/plain" }), TEST_ENV)).status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("advertises Idempotency-Key in preflight for catalog POST paths only", async () => {
    for (const path of ["v2/commands/create-product", "v2/commands/add-product-image"]) {
      const res = await handleRequest(
        new Request(`${DASHBOARD}/api/${path}`, { method: "OPTIONS", headers: { Origin: DASHBOARD } }),
        TEST_ENV,
      );
      expect(res.headers.get("Access-Control-Allow-Methods"), path).toContain("POST");
      expect(res.headers.get("Access-Control-Allow-Headers"), path).toContain("Idempotency-Key");
    }
    const read = await handleRequest(
      new Request(`${DASHBOARD}/api/v2/catalog/products`, { method: "OPTIONS", headers: { Origin: DASHBOARD } }),
      TEST_ENV,
    );
    expect(read.headers.get("Access-Control-Allow-Methods")).not.toContain("POST");
    expect(read.headers.get("Access-Control-Allow-Headers")).not.toContain("Idempotency-Key");
  });
});
