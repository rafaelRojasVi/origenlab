import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLeave } from "./useLeave";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useLeave", () => {
  it("marks the row leaving at once and gone after 180 ms", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useLeave());
    act(() => result.current.leave());
    expect(result.current).toMatchObject({ leaving: true, gone: false });
    act(() => vi.advanceTimersByTime(180));
    expect(result.current.gone).toBe(true);
  });

  it("is gone immediately when the user prefers reduced motion", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
    const { result } = renderHook(() => useLeave());
    act(() => result.current.leave());
    expect(result.current.gone).toBe(true);
  });
});
