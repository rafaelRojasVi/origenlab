import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LEAVE_MS, useMediaQuery, usePresence } from "./motion";

function mockMedia(matching: (query: string) => boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: matching(query),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const key = (s: string) => s;

describe("usePresence", () => {
  it("keeps a leaving item in its place for the fade, then drops it", () => {
    vi.useFakeTimers();
    mockMedia(() => false);
    const { result, rerender } = renderHook(({ items }) => usePresence(items, key), { initialProps: { items: ["a", "b", "c"] } });
    rerender({ items: ["a", "c"] });
    expect(result.current.map((x) => [x.key, x.leaving])).toEqual([["a", false], ["b", true], ["c", false]]);
    act(() => vi.advanceTimersByTime(LEAVE_MS + 1));
    expect(result.current.map((x) => x.key)).toEqual(["a", "c"]);
  });

  it("drops it at once with reduced motion", () => {
    mockMedia((q) => q.includes("prefers-reduced-motion"));
    const { result, rerender } = renderHook(({ items }) => usePresence(items, key), { initialProps: { items: ["a", "b"] } });
    rerender({ items: ["a"] });
    expect(result.current.map((x) => [x.key, x.leaving])).toEqual([["a", false]]);
  });

  it("an item that comes back is not leaving", () => {
    vi.useFakeTimers();
    mockMedia(() => false);
    const { result, rerender } = renderHook(({ items }) => usePresence(items, key), { initialProps: { items: ["a", "b"] } });
    rerender({ items: ["a"] });
    rerender({ items: ["a", "b"] });
    expect(result.current.map((x) => [x.key, x.leaving])).toEqual([["a", false], ["b", false]]);
  });
});

describe("useMediaQuery", () => {
  it("reads the query", () => {
    mockMedia((q) => q === "(max-width: 639px)");
    expect(renderHook(() => useMediaQuery("(max-width: 639px)")).result.current).toBe(true);
    expect(renderHook(() => useMediaQuery("(min-width: 1px)")).result.current).toBe(false);
  });
});
