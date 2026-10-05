import { type RefObject, useEffect, useRef, useState } from "react";

/**
 * True once the element has come near the viewport, and from then on. For reads that only a
 * visible element needs — a campaign thumbnail is one request per card, each a session check and
 * a database round trip ~180 ms away — so a long list asks only for what is scrolled to.
 *
 * Without `IntersectionObserver` (an old browser, a test environment) the element counts as
 * visible at once: the page loads everything, as it did before, rather than nothing.
 */
export function useInView<T extends Element>(rootMargin = "200px"): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (inView) return;
    const element = ref.current;
    if (element === null || typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [inView, rootMargin]);

  return [ref, inView];
}
