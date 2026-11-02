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

  it("brings the row back when restored mid-exit, and the pending exit does not finish", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useLeave());
    act(() => result.current.leave());
    act(() => result.current.restore());
    expect(result.current).toMatchObject({ leaving: false, gone: false });
    act(() => vi.advanceTimersByTime(500));
    expect(result.current.gone).toBe(false);
  });

  it("brings back a row that had already gone", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useLeave());
    act(() => result.current.leave());
    act(() => vi.advanceTimersByTime(180));
    act(() => result.current.restore());
    expect(result.current).toMatchObject({ leaving: false, gone: false });
  });
});
