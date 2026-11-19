/**
 * Light list motion for «Seguimiento»: items that leave stay a moment to fade out (`usePresence`),
 * items that move glide to their new place and new ones rise in (`useFlip`, FLIP with the Web
 * Animations API: transform and opacity only, so nothing around them shifts). All of it is off
 * under `prefers-reduced-motion`.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { prefersReducedMotion } from "./useLeave";

export const LEAVE_MS = 180;
const MOVE_MS = 220;
const ENTER_MS = 200;
const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";

/** Whether a media query matches now, and again whenever it changes. */
export function useMediaQuery(query: string): boolean {
  const read = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [query]);
  return matches;
}

/**
 * `items`, plus for `LEAVE_MS` the ones that just left, flagged `leaving` and kept where they
 * were, so they can fade out (`crm-row-out`) instead of vanishing. With reduced motion they go at once.
 */
export function usePresence<T>(items: T[], keyOf: (item: T) => string): { item: T; key: string; leaving: boolean }[] {
  const prev = useRef<T[]>(items);
  const [leavers, setLeavers] = useState<{ item: T; key: string; index: number }[]>([]);
  const timers = useRef<number[]>([]);
  const keyRef = useRef(keyOf);
  keyRef.current = keyOf;

  useLayoutEffect(() => {
    const keyFn = keyRef.current;
    const present = new Set(items.map(keyFn));
    const gone = prev.current
      .map((item, index) => ({ item, key: keyFn(item), index }))
      .filter((x) => !present.has(x.key));
    prev.current = items;
    if (gone.length === 0 || prefersReducedMotion()) {
      // Someone who came back is not leaving any more.
      setLeavers((l) => (l.some((x) => present.has(x.key)) ? l.filter((x) => !present.has(x.key)) : l));
      return;
    }
    const goneKeys = new Set(gone.map((g) => g.key));
    setLeavers((l) => [...l.filter((x) => !present.has(x.key) && !goneKeys.has(x.key)), ...gone]);
    timers.current.push(window.setTimeout(() => setLeavers((l) => l.filter((x) => !goneKeys.has(x.key))), LEAVE_MS));
  }, [items]);

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  return useMemo(() => {
    const out = items.map((item) => ({ item, key: keyRef.current(item), leaving: false }));
    const present = new Set(out.map((x) => x.key));
    for (const l of [...leavers].sort((a, b) => a.index - b.index)) {
      if (present.has(l.key)) continue;
      out.splice(Math.min(l.index, out.length), 0, { item: l.item, key: l.key, leaving: true });
    }
    return out;
  }, [items, leavers]);
}

/**
 * After each change of `signature` (the order of the keys), every `[data-flip]` element under
 * `ref` that moved glides from its old place to the new one, and a new one rises in. The first
 * render does not animate.
 */
export function useFlip(ref: RefObject<HTMLElement | null>, signature: string): void {
  const last = useRef<Map<string, DOMRect> | null>(null);
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const els = Array.from(root.querySelectorAll<HTMLElement>("[data-flip]"));
    const next = new Map<string, DOMRect>();
    for (const el of els) next.set(el.dataset.flip!, el.getBoundingClientRect());
    const before = last.current;
    last.current = next;
    if (!before || prefersReducedMotion()) return;
    for (const el of els) {
      if (typeof el.animate !== "function") continue;
      const was = before.get(el.dataset.flip!);
      const now = next.get(el.dataset.flip!)!;
      if (!was) {
        el.animate([{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }], { duration: ENTER_MS, easing: EASE });
        continue;
      }
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: MOVE_MS, easing: EASE });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
}
