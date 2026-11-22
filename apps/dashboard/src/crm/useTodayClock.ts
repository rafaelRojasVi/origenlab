import { useEffect, useMemo, useState } from "react";

/**
 * «Now» for a page that sorts work by day (Hoy): read again each time `data` changes — a reload, a
 * save — as the Pipeline does, and again when the local day turns while the page sits open: at
 * midnight, and when the tab comes back on another day (a laptop that slept overnight fires no
 * timer on time). A tab opened on Tuesday never keeps Tuesday's clock on Wednesday (audit
 * 2026-11-22).
 */
export function useTodayClock(data: unknown): Date {
  const [turn, setTurn] = useState(0);
  // `data` and `turn` are the reasons to read the clock again, not inputs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => new Date(), [data, turn]);

  useEffect(() => {
    const bump = () => setTurn((n) => n + 1);
    const otherDay = () => new Date().toDateString() !== now.toDateString();
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    // A second past midnight, so the new day has surely begun.
    const timer = window.setTimeout(bump, Math.max(1_000, midnight.getTime() - Date.now() + 1_000));
    const onVisible = () => {
      if (otherDay()) bump();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [now]);

  return now;
}
