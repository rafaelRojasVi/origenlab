import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateImageUrl, requestImageUrl, resetImageUrls } from "./imageUrls";

function okBody(ids: string[], expires = 600) {
  return new Response(JSON.stringify({ urls: Object.fromEntries(ids.map((i) => [i, `https://s.test/${i}`])),
    expires_in: expires }), { status: 200 });
}
const idsOf = (call: unknown[]) => new URL(String(call[0]), "http://x.test").searchParams.get("ids")!.split(",");

describe("requestImageUrl", () => {
  beforeEach(() => resetImageUrls());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("batches the ids asked for together into one request", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(okBody(idsOf([url]))));
    vi.stubGlobal("fetch", fetchMock);
    const urls = await Promise.all(["a", "b", "c"].map(requestImageUrl));
    expect(urls).toEqual(["https://s.test/a", "https://s.test/b", "https://s.test/c"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(idsOf(fetchMock.mock.calls[0])).toEqual(["a", "b", "c"]);
  });

  it("splits more than sixty ids into chunks", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(okBody(idsOf([url]))));
    vi.stubGlobal("fetch", fetchMock);
    await Promise.all(Array.from({ length: 61 }, (_, i) => requestImageUrl(`i${i}`)));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("answers from cache until shortly before expiry", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(okBody(idsOf([url]), 600)));
    vi.stubGlobal("fetch", fetchMock);
    await requestImageUrl("a");
    await requestImageUrl("a");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    invalidateImageUrl("a");
    await requestImageUrl("a");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("an id the API leaves out resolves null", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(okBody([]))));
    expect(await requestImageUrl("gone")).toBeNull();
  });

  it("after a 503 every image is a placeholder and nothing more is fetched", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("{}", { status: 503 })));
    vi.stubGlobal("fetch", fetchMock);
    expect(await Promise.all([requestImageUrl("a"), requestImageUrl("b")])).toEqual([null, null]);
    expect(await requestImageUrl("c")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
