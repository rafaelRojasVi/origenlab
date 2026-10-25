import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, readThemeChoice, useTheme } from "./theme";

function mockSystemDark(dark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: dark, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
}

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

describe("theme", () => {
  it("defaults to the system setting", () => {
    expect(readThemeChoice()).toBe("system");
    mockSystemDark(true);
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("an explicit choice wins over the system", () => {
    mockSystemDark(true);
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("remembers the choice and forgets it on «Sistema»", () => {
    mockSystemDark(false);
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("dark"));
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    act(() => result.current[1]("system"));
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("ignores an unknown stored value", () => {
    window.localStorage.setItem(THEME_KEY, "sepia");
    expect(readThemeChoice()).toBe("system");
  });
});
