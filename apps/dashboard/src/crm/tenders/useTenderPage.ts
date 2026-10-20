import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTenderPage, type TenderPageData } from "./tenderApi";

/** While a preparation is queued or running, the page is read again this often. */
export const POLL_MS = 5000;

export type TenderPageState =
  | { kind: "loading" }
  | { kind: "ready"; data: TenderPageData }
  | { kind: "error"; error: unknown };

/** True while the worker still has a phase of this tender in hand. */
export function preparationBusy(data: TenderPageData): boolean {
  return (["read", "fill"] as const).some((p) => {
    const status = data.preparations[p]?.status;
    return status === "queued" || status === "running";
  });
}

/**
 * One tender's page, read once and then every `pollMs` only while a preparation is queued or
 * running. Reads never overlap: the next poll is scheduled when a read ends, and a `reload` asked
 * for during a read runs once it is over. Everything stops on unmount; `enabled = false` (a viewer)
 * reads nothing at all.
 */
export function useTenderPage(id: string, enabled: boolean, pollMs: number = POLL_MS): [TenderPageState, () => void] {
  const [state, setState] = useState<TenderPageState>({ kind: "loading" });
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const again = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busy = useRef(false);
  const hasData = useRef(false);

  const load = useCallback(async () => {
    if (!enabled || !mounted.current) return;
    if (inFlight.current) {
      again.current = true;
      return;
    }
    inFlight.current = true;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    try {
      const data = await fetchTenderPage(id);
      if (!mounted.current) return;
      hasData.current = true;
      busy.current = preparationBusy(data);
      setState({ kind: "ready", data });
    } catch (error) {
      if (!mounted.current) return;
      // A failed poll keeps what is on screen and tries again at the next tick.
      if (!hasData.current) {
        busy.current = false;
        setState({ kind: "error", error });
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) {
        if (again.current) {
          again.current = false;
          void load();
        } else if (busy.current) {
          timer.current = setTimeout(() => void load(), pollMs);
        }
      }
    }
  }, [id, enabled, pollMs]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [load]);

  const reload = useCallback(() => {
    if (!hasData.current) setState({ kind: "loading" });
    void load();
  }, [load]);

  return [state, reload];
}
