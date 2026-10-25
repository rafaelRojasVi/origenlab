import { useCallback, useEffect, useState } from "react";

/**
 * Light or dark: the operator's choice, remembered on this browser. «Sistema» follows the
 * computer's setting and changes with it. index.html applies the stored choice before the
 * first paint, so a dark page never flashes white; this module keeps it in step afterwards.
 */
export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "crm.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

export function readThemeChoice(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function applyTheme(choice: ThemeChoice): void {
  const dark = choice === "dark" || (choice === "system" && systemDark());
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(readThemeChoice);

  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = () => applyTheme("system");
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [choice]);

  const choose = useCallback((c: ThemeChoice) => {
    try {
      if (c === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, c);
    } catch {
      /* the choice just won't be remembered */
    }
    setChoice(c);
  }, []);

  return [choice, choose];
}
