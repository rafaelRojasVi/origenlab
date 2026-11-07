import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorApiError } from "../../api/operatorClient";
import { fetchModelSheet, fetchPublishStatus, publishModelSheet, publishRefusal } from "./webPublishApi";

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchModelSheet", () => {
  it("reads GET /v2/web/model-sheet/{id}", async () => {
    const read = { model_id: "up400st", sheet: {}, sha: "a", html_url: "https://github.com/x", valid: true, errors: [] };
    const fn = stubFetch(200, read);
    await expect(fetchModelSheet("up400st")).resolves.toEqual(read);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v2/web/model-sheet/up400st");
    expect(init.method).toBe("GET");
  });

  it("answers null for the API's «sheet_not_found», and throws for any other 404", async () => {
    stubFetch(404, { code: "sheet_not_found" });
    await expect(fetchModelSheet("nuevo")).resolves.toBeNull();
    stubFetch(404, { detail: "Not Found" });
    await expect(fetchModelSheet("nuevo")).rejects.toBeInstanceOf(OperatorApiError);
  });
});

describe("publishModelSheet", () => {
  it("POSTs {model_id, sheet} as JSON with the session cookie", async () => {
    const fn = stubFetch(200, { outcome: "dispatched", run_url: "https://github.com/x/runs/1", commit_sha: "abc" });
    await publishModelSheet({ model_id: "up400st", sheet: { slug: "up400st" } });
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v2/web/publish-model-sheet");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body as string)).toEqual({ model_id: "up400st", sheet: { slug: "up400st" } });
  });

  it("throws the refusal, and publishRefusal reads it from detail, error.details or the body itself", async () => {
    stubFetch(422, { detail: { code: "sheet_invalid", message: "m", errors: [{ path: "lead", message: "too long" }] } });
    const a = publishRefusal(await publishModelSheet({ model_id: "x", sheet: {} }).catch((e) => e));
    expect(a).toEqual({ status: 422, code: "sheet_invalid", errors: [{ path: "lead", message: "too long" }], commitSha: null });

    stubFetch(422, { error: { code: "validation_error", message: "m", details: { code: "sheet_invalid", errors: [{ path: "name", message: "x" }] } } });
    const b = publishRefusal(await publishModelSheet({ model_id: "x", sheet: {} }).catch((e) => e));
    expect(b?.code).toBe("sheet_invalid");
    expect(b?.errors).toEqual([{ path: "name", message: "x" }]);

    stubFetch(502, { code: "dispatch_failed", commit_sha: "abc1234" });
    const c = publishRefusal(await publishModelSheet({ model_id: "x", sheet: {} }).catch((e) => e));
    expect(c).toMatchObject({ status: 502, code: "dispatch_failed", commitSha: "abc1234" });

    expect(publishRefusal(new Error("boom"))).toBeNull();
  });
});

describe("fetchPublishStatus", () => {
  it("reads GET /v2/web/publish-status/{id}", async () => {
    const fn = stubFetch(200, { status: "running", run_url: null, updated_at: null, head_sha: null });
    await expect(fetchPublishStatus("up400st")).resolves.toMatchObject({ status: "running" });
    expect((fn.mock.calls[0] as unknown as [string])[0]).toContain("/v2/web/publish-status/up400st");
  });
});
