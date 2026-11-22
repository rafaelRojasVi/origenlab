import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTodayClock } from "./useTodayClock";

afterEach(() => {
  vi.useRealTimers();
});

describe("useTodayClock", () => {
  it("turns to the new day at midnight while the page sits open", () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date(2026, 10, 17, 22, 0)); // Tuesday 22:00, local
    const { result } = renderHook(() => useTodayClock("same data"));
    expect(result.current.getDate()).toBe(17);
    act(() => {
      vi.advanceTimersByTime(2 * 3_600_000 + 2_000);
    });
    expect(result.current.getDate()).toBe(18);
  });

  it("reads the clock again when the data changes", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 17, 10, 0));
    const { result, rerender } = renderHook(({ data }) => useTodayClock(data), { initialProps: { data: 1 } });
    vi.setSystemTime(new Date(2026, 10, 17, 15, 0));
    rerender({ data: 1 });
    expect(result.current.getHours()).toBe(10);
    rerender({ data: 2 });
    expect(result.current.getHours()).toBe(15);
  });

  it("a tab that comes back on another day reads the new day", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 17, 22, 0));
    const { result } = renderHook(() => useTodayClock("same data"));
    vi.setSystemTime(new Date(2026, 10, 18, 8, 0));
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(result.current.getDate()).toBe(18);
  });
});
