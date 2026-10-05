import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorApiError, fetchJsonGet, getWriteGeneration, noteWrite } from "./operatorClient";

// Every value below is invented; the repository is public.
const URL_A = "http://127.0.0.1:8001/v2/workspace/pipeline";

function deferredResponse() {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function okResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

afterEach(() => {
  noteWrite();
  vi.unstubAllGlobals();
});

describe("fetchJsonGet shares a read while it is in flight", () => {
  it("two callers of one URL at once make one request and get separate copies", async () => {
    const pending = deferredResponse();
    const fetchMock = vi.fn().mockReturnValue(pending.promise);
    vi.stubGlobal("fetch", fetchMock);

    const first = fetchJsonGet<{ items: string[] }>(URL_A);
    const second = fetchJsonGet<{ items: string[] }>(URL_A);
    pending.resolve(okResponse({ items: ["caso-1"] }));
    const [a, b] = await Promise.all([first, second]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toEqual({ items: ["caso-1"] });
    expect(b).toEqual(a);
    expect(b).not.toBe(a);
    a.items.push("cambiado");
    expect(b.items).toEqual(["caso-1"]);
  });

  it("asks again once the first request has answered", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => okResponse({ n: fetchMock.mock.calls.length }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchJsonGet(URL_A)).toEqual({ n: 1 });
    expect(await fetchJsonGet(URL_A)).toEqual({ n: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("different URLs are never shared", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => okResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    await Promise.all([fetchJsonGet(URL_A), fetchJsonGet(`${URL_A}?limit=5`)]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("a read that starts after a write never joins one requested before it", async () => {
    const before = deferredResponse();
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(before.promise)
      .mockResolvedValueOnce(okResponse({ state: "after" }));
    vi.stubGlobal("fetch", fetchMock);

    const generation = getWriteGeneration();
    const stale = fetchJsonGet(URL_A);
    noteWrite();
    expect(getWriteGeneration()).toBe(generation + 1);
    const fresh = fetchJsonGet(URL_A);
    before.resolve(okResponse({ state: "before" }));

    expect(await stale).toEqual({ state: "before" });
    expect(await fresh).toEqual({ state: "after" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("a refusal reaches every caller and is not kept", async () => {
    const pending = deferredResponse();
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(okResponse({ ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const first = fetchJsonGet(URL_A);
    const second = fetchJsonGet(URL_A);
    pending.resolve({ ok: false, status: 503, statusText: "no", text: async () => "caído" } as Response);
    await expect(first).rejects.toBeInstanceOf(OperatorApiError);
    await expect(second).rejects.toBeInstanceOf(OperatorApiError);
    expect(await fetchJsonGet(URL_A)).toEqual({ ok: 1 });
  });
});
