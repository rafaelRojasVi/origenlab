import { useCallback, useEffect, useRef, useState } from "react";

/**
 * A long list drawn in slices: the first `step` items, and `step` more each time its end scrolls
 * into view (or «Mostrar más» is pressed). The page keeps every item for counts, totals and search;
 * only the DOM is kept small. Where `IntersectionObserver` does not exist the whole list is drawn.
 */
export function useGrowingList<T>(items: readonly T[], step = 40): {
  shown: readonly T[];
  hidden: number;
  showMore: () => void;
  endRef: (el: Element | null) => void;
} {
  const [limit, setLimit] = useState(step);
  const observer = useRef<IntersectionObserver | null>(null);
  const showMore = useCallback(() => setLimit((n) => n + step), [step]);
  const endRef = useCallback(
    (el: Element | null) => {
      observer.current?.disconnect();
      observer.current = null;
      if (!el || typeof IntersectionObserver === "undefined") return;
      observer.current = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) showMore();
      });
      observer.current.observe(el);
    },
    [showMore],
  );
  useEffect(() => () => observer.current?.disconnect(), []);
  if (typeof IntersectionObserver === "undefined") return { shown: items, hidden: 0, showMore, endRef };
  const shown = items.length > limit ? items.slice(0, limit) : items;
  return { shown, hidden: items.length - shown.length, showMore, endRef };
}
