import { useCallback, useEffect, useRef, useState } from "react";

const LEAVE_MS = 180;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * A row that has done its job leaves now: `leaving` starts its 180 ms exit (`crm-row-out`),
 * `gone` drops it. The reload that confirms the change runs behind it.
 *
 * `data` is the row's own record. A reload that still returns the row hands it a new record, and
 * the row shows again: a change that did not take it off the list must not hide it.
 */
export function useLeave(data?: unknown): { leaving: boolean; gone: boolean; leave: () => void } {
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);
  const timer = useRef<number | null>(null);
  const seen = useRef(data);
  useEffect(() => {
    if (Object.is(seen.current, data)) return;
    seen.current = data;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setLeaving(false);
    setGone(false);
  }, [data]);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  const leave = useCallback(() => {
    setLeaving(true);
    if (prefersReducedMotion()) {
      setGone(true);
      return;
    }
    timer.current = window.setTimeout(() => setGone(true), LEAVE_MS);
  }, []);
  return { leaving, gone, leave };
}
